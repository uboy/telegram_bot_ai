"""
Обработчики callback'ов для кнопок
"""
from shared.types import UserContext
import os
import tempfile
import re
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes
import asyncio
from shared.database import Session, User, KnowledgeBase, KnowledgeChunk, KnowledgeImportLog
from shared.ai_providers import ai_manager
from shared.document_loaders import document_loader_manager
from shared.image_processor import image_processor
from frontend.templates.buttons import (
    main_menu,
    admin_menu,
    settings_menu,
    ai_providers_menu,
    ollama_models_menu,
    user_management_menu,
    knowledge_base_menu,
    kb_actions_menu,
    document_type_menu,
    confirm_menu,
    n8n_menu,
    rag_settings_menu,
    kb_settings_menu,
    search_options_menu,
    search_filters_menu,
    summary_mode_menu,
)
from frontend.backend_client import backend_client
try:
    from shared.config import ADMIN_IDS, N8N_PUBLIC_URL
except ImportError:
    # Fallback если config.py не найден
    import os
    ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_STR.split(",") if id.strip()] if ADMIN_IDS_STR else []
    N8N_PUBLIC_URL = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678")
from shared.logging_config import logger
from shared.n8n_client import n8n_client

# Строковые id админов для безопасных сравнений
ADMIN_ID_STRINGS = {str(x) for x in ADMIN_IDS}

# Глобальный session удалён - создаём session локально в функциях


def update_env_file(var_name: str, var_value: str) -> bool:
    """Обновить переменную окружения в .env файле"""
    env_file_path = ".env"
    
    if not os.path.exists(env_file_path):
        logger.warning(f"Файл .env не найден, создаю новый")
        try:
            with open(env_file_path, 'w', encoding='utf-8') as f:
                f.write(f"# Auto-generated .env file\n")
                f.write(f"{var_name}={var_value}\n")
            return True
        except Exception as e:
            logger.error(f"Ошибка создания .env файла: {e}")
            return False
    
    try:
        pattern = re.compile(rf'^\s*{re.escape(var_name)}\s*=')
        commented_pattern = re.compile(rf'^\s*#\s*{re.escape(var_name)}\s*=')
        is_rag_var = var_name.startswith("RAG_")
        # Читаем весь файл
        with open(env_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Ищем переменную и обновляем её значение
        found = False
        has_rag_section = False
        updated_lines = []
        rag_section_line_idx = None
        
        # Проверяем наличие секции # RAG Configuration
        for idx, line in enumerate(lines):
            if line.strip() == "# RAG Configuration":
                has_rag_section = True
                rag_section_line_idx = idx
                break
        
        for line in lines:
            stripped = line.strip()
            # Проверяем, является ли строка нашей переменной (с учетом комментариев)
            if pattern.match(line) and not line.lstrip().startswith('#'):
                # Если значение содержит пробелы, экранируем кавычками
                if ' ' in var_value:
                    # Экранируем кавычки в значении
                    escaped_value = var_value.replace('"', '\\"')
                    updated_lines.append(f'{var_name}="{escaped_value}"\n')
                else:
                    updated_lines.append(f"{var_name}={var_value}\n")
                found = True
            elif commented_pattern.match(line):
                # Если переменная закомментирована, раскомментируем и обновим
                if ' ' in var_value:
                    escaped_value = var_value.replace('"', '\\"')
                    updated_lines.append(f'{var_name}="{escaped_value}"\n')
                else:
                    updated_lines.append(f"{var_name}={var_value}\n")
                found = True
            else:
                updated_lines.append(line)
        
        # Если переменная не найдена, добавляем в конец
        if not found:
            # Подготовим строку значения
            if ' ' in var_value:
                escaped_value = var_value.replace('"', '\\"')
                new_line = f'{var_name}="{escaped_value}"\n'
            else:
                new_line = f"{var_name}={var_value}\n"

            if is_rag_var and has_rag_section and rag_section_line_idx is not None:
                # Вставляем сразу после секции RAG (или в конец секции)
                # Найдём позицию: после последней RAG_* строки следующей за # RAG Configuration
                insert_at = None
                for j in range(rag_section_line_idx + 1, len(updated_lines)):
                    t = updated_lines[j].strip()
                    if not t or t.startswith("#"):
                        continue
                    if not t.startswith("RAG_"):
                        insert_at = j
                        break
                if insert_at is None:
                    insert_at = len(updated_lines)
                updated_lines.insert(insert_at, new_line)
            else:
                # Добавляем секцию только если её ещё нет
                if is_rag_var and not has_rag_section:
                    updated_lines.append("\n# RAG Configuration\n")
                updated_lines.append(new_line)
        
        # Записываем обратно
        with open(env_file_path, 'w', encoding='utf-8') as f:
            f.writelines(updated_lines)
        
        logger.info(f"Обновлена переменная {var_name} в .env файле: {var_value}")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления .env файла: {e}", exc_info=True)
        return False


async def safe_edit_message_text(query, text: str, reply_markup=None, parse_mode=None):
    """Безопасное редактирование сообщения с обработкой ошибок

    parse_mode прокидывается во все вызовы edit_message_text/reply_text, чтобы
    можно было безопасно использовать HTML/Markdown.
    """
    from telegram import ReplyKeyboardMarkup
    
    # edit_message_text не поддерживает ReplyKeyboardMarkup, только InlineKeyboardMarkup
    # Если передан ReplyKeyboardMarkup, сразу отправляем новое сообщение
    if reply_markup and isinstance(reply_markup, ReplyKeyboardMarkup):
        try:
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            await query.delete_message()
            return
        except Exception as e:
            logger.error("Не удалось отправить сообщение с ReplyKeyboardMarkup: %s", e)
            await query.answer("Ошибка отправки сообщения. Пожалуйста, отправьте /start.", show_alert=True)
            return
    
    # Для InlineKeyboardMarkup пытаемся отредактировать сообщение
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        error_msg = str(e).lower()
        # ВАЖНО: "message is not modified" - не ошибка, просто игнорируем
        if 'message is not modified' in error_msg:
            return
        
        if 'button_data_invalid' in error_msg or 'inline keyboard expected' in error_msg:
            # Старые кнопки или невалидный формат - отправляем новое сообщение
            logger.warning("Не удалось отредактировать сообщение (старые кнопки?), отправляю новое: %s", e)
            try:
                # Для InlineKeyboardMarkup можно использовать
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
                await query.delete_message()
            except Exception as e2:
                logger.error("Не удалось отправить новое сообщение: %s", e2)
                # Попробуем просто ответить без клавиатуры
                try:
                    await query.message.reply_text(text, parse_mode=parse_mode)
                    await query.delete_message()
                except Exception as e3:
                    logger.error("Не удалось отправить сообщение даже без клавиатуры: %s", e3)
                    await query.answer("Эта кнопка устарела. Пожалуйста, отправьте /start для обновления меню.", show_alert=True)
        else:
            raise


def _n8n_status_text() -> str:
    """Сформировать текст статуса интеграции n8n."""
    lines = ["🤖 Интеграция n8n"]
    base_url = n8n_client.base_url or "—"
    lines.append(f"Базовый URL: {base_url}")
    lines.append(f"Webhook: {'настроен' if n8n_client.has_webhook() else 'не указан'}")
    lines.append(
        "API-ключ: настроен" if n8n_client.api_key else "API-ключ: не указан (нужен только для запуска workflow)"
    )
    lines.append("")
    lines.append("n8n используется для автоматизации процессов (webhook после загрузок, тестовые события и т.д.).")
    lines.append("Настройте переменные окружения N8N_BASE_URL и N8N_DEFAULT_WEBHOOK, чтобы включить интеграцию.")
    return "\n".join(lines)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик callback'ов"""
    query = update.callback_query
    if not query:
        return
    
    # Пытаемся ответить на callback query (если он еще валиден)
    try:
        await query.answer()
    except BadRequest as e:
        error_msg = str(e).lower()
        if 'query is too old' in error_msg or 'query id is invalid' in error_msg:
            # Query слишком старый или невалидный - просто игнорируем
            logger.debug(f"Callback query слишком старый или невалидный: {e}")
            return
        else:
            # Другая ошибка - логируем и продолжаем
            logger.warning(f"Ошибка при ответе на callback query: {e}")
    
    data = query.data
    
    # Обработка невалидных callback_data (старые кнопки)
    if not data:
        try:
            await query.answer("Эта кнопка устарела. Пожалуйста, отправьте /start для обновления меню.", show_alert=True)
        except BadRequest:
            pass  # Query уже обработан или слишком старый
        return
    
    user_id = str(query.from_user.id)
    username = query.from_user.username if query.from_user else None
    full_name = getattr(query.from_user, "full_name", None) if query.from_user else None
    
    # ВАЖНО: Права пользователя/approved берём только из backend, локальная БД — кэш.
    backend_user = backend_client.auth_telegram(
        telegram_id=user_id,
        username=username,
        full_name=full_name,
    )
    
    if not backend_user or (not backend_user.get("approved") and backend_user.get("role") != "admin"):
        await safe_edit_message_text(query, "Вы не одобрены для использования бота.")
        return
    
    role = backend_user.get("role") or "user"
    approved = bool(backend_user.get("approved", False))
    
    # Получаем preferred_* из локальной БД для кэша (если нужно)
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        preferred_provider = user.preferred_provider if user else None
        preferred_model = user.preferred_model if user else None
        preferred_image_model = getattr(user, "preferred_image_model", None) if user else None
    finally:
        session.close()
    
    # Обработка одобрения/отклонения пользователей (только для админов)
    if data.startswith("approve:") or data.startswith("decline:"):
        if user_id not in ADMIN_ID_STRINGS:
            return

        _, tg_id = data.split(":")

        # Получаем список пользователей из backend и ищем по telegram_id
        users = await asyncio.to_thread(backend_client.list_users)
        target = next((u for u in users if str(u.get("telegram_id")) == str(tg_id)), None)
        if not target:
            await safe_edit_message_text(query, "Пользователь не найден в backend.")
            return

        target_internal_id = target.get("id")
        if not target_internal_id:
            await safe_edit_message_text(query, "Некорректные данные пользователя.")
            return

        if data.startswith("approve:"):
            ok = await asyncio.to_thread(backend_client.toggle_user_role, int(target_internal_id))
            if ok:
                await safe_edit_message_text(query, "✅ Пользователь одобрен")
                try:
                    await context.bot.send_message(
                        chat_id=int(tg_id),
                        text="✅ Ваша заявка одобрена! Теперь вы можете использовать бота.",
                        reply_markup=main_menu(),
                    )
                except Exception:
                    pass
            else:
                await safe_edit_message_text(query, "❌ Не удалось одобрить пользователя через backend.")
        else:
            ok = await asyncio.to_thread(backend_client.delete_user, int(target_internal_id))
            if ok:
                await safe_edit_message_text(query, "❌ Пользователь отклонен")
            else:
                await safe_edit_message_text(query, "❌ Не удалось отклонить пользователя через backend.")
        return
    
    # Главное меню
    if data == 'main_menu':
        menu = main_menu(is_admin=(role == 'admin'))
        # main_menu возвращает ReplyKeyboardMarkup, поэтому отправляем новое сообщение
        try:
            await query.message.reply_text("Выберите действие:", reply_markup=menu)
            await query.delete_message()
        except Exception as e:
            logger.warning("Ошибка при отправке главного меню: %s", e)
            # Если не удалось удалить старое сообщение, просто отправим новое
            try:
                await query.message.reply_text("Выберите действие:", reply_markup=menu)
            except Exception:
                await query.answer("Пожалуйста, отправьте /start для обновления меню.", show_alert=True)
        return
    
    # Настройки
    if data == 'settings':
        session = Session()
        try:
            user_db = session.query(User).filter_by(telegram_id=user_id).first()
            show_meta = user_db.show_asr_metadata if user_db else True
            await safe_edit_message_text(query, "⚙️ Настройки:", reply_markup=settings_menu(show_asr_metadata=show_meta))
        finally:
            session.close()
        return

    if data == 'toggle_asr_metadata':
        session = Session()
        try:
            user_db = session.query(User).filter_by(telegram_id=user_id).first()
            if user_db:
                user_db.show_asr_metadata = not user_db.show_asr_metadata
                session.commit()
                show_meta = user_db.show_asr_metadata
                await safe_edit_message_text(
                    query, 
                    f"⚙️ Настройки:\n\nТехническая информация ASR: {'ВКЛ' if show_meta else 'ВЫКЛ'}", 
                    reply_markup=settings_menu(show_asr_metadata=show_meta)
                )
            else:
                await query.answer("Пользователь не найден")
        finally:
            session.close()
        return
    
    # Выбор провайдера ИИ
    if data == 'select_provider':
        providers = ai_manager.list_providers()
        current = ai_manager.current_provider or 'ollama'
        await safe_edit_message_text(query, "🤖 Выберите провайдер ИИ:", reply_markup=ai_providers_menu(providers, current))
        return
    
    if data.startswith('provider:'):
        provider_name = data.split(':', 1)[1]
        if ai_manager.set_provider(provider_name):
            session = Session()
            try:
                user_db = session.query(User).filter_by(telegram_id=user_id).first()
                if user_db:
                    user_db.preferred_provider = provider_name
                    session.commit()
                    preferred_provider = provider_name  # Обновляем локальную переменную
            finally:
                session.close()
            
            # Если выбран Ollama, можно дальше выбрать модели в настройках
            if provider_name == 'ollama':
                await safe_edit_message_text(
                    query,
                    "✅ Провайдер изменен на Ollama.\nТеперь выберите модели для текста и изображений в настройках.",
                    reply_markup=settings_menu(),
                )
            else:
                await safe_edit_message_text(query, f"✅ Провайдер изменен на {provider_name}", reply_markup=settings_menu())
        else:
            await query.answer("Ошибка выбора провайдера", show_alert=True)
        return
    
    # Выбор моделей Ollama
    if data == 'select_text_model':
        try:
            provider = ai_manager.get_provider('ollama')
            if not provider:
                logger.warning("Провайдер Ollama не найден в ai_manager")
                await safe_edit_message_text(
                    query,
                    "❌ Провайдер Ollama недоступен. Проверьте настройки OLLAMA_BASE_URL.",
                    reply_markup=settings_menu(),
                )
                return
            
            if not hasattr(provider, 'list_models'):
                logger.warning("Провайдер Ollama не имеет метода list_models")
                await safe_edit_message_text(
                    query,
                    "❌ Провайдер Ollama не поддерживает список моделей.",
                    reply_markup=settings_menu(),
                )
                return
            
            models = provider.list_models()
            logger.info(f"Получен список моделей Ollama: {models}")
            
            if not models:
                logger.warning("Список моделей Ollama пуст")
                await safe_edit_message_text(
                    query,
                    "❌ Не удалось загрузить список моделей Ollama.\n\nПроверьте:\n1. Запущен ли Ollama сервер\n2. Правильно ли настроен OLLAMA_BASE_URL\n3. Есть ли модели в Ollama",
                    reply_markup=settings_menu(),
                )
                return
            
            current_model = preferred_model or (provider.model if hasattr(provider, 'model') else '')
            logger.info(f"Текущая модель для текста: {current_model}")
            
            await safe_edit_message_text(
                query,
                f"💬 Выберите модель Ollama для текстовых запросов:\n\nТекущая: {current_model or 'не выбрана'}",
                reply_markup=ollama_models_menu(models, current_model, target='text'),
            )
        except Exception as e:
            logger.error(f"Ошибка при получении списка моделей Ollama: {e}", exc_info=True)
            await safe_edit_message_text(
                query,
                f"❌ Ошибка при загрузке списка моделей: {str(e)}",
                reply_markup=settings_menu(),
            )
        return

    if data == 'select_image_model':
        try:
            provider = ai_manager.get_provider('ollama')
            if not provider:
                logger.warning("Провайдер Ollama не найден в ai_manager")
                await safe_edit_message_text(
                    query,
                    "❌ Провайдер Ollama недоступен. Проверьте настройки OLLAMA_BASE_URL.",
                    reply_markup=settings_menu(),
                )
                return
            
            if not hasattr(provider, 'list_models'):
                logger.warning("Провайдер Ollama не имеет метода list_models")
                await safe_edit_message_text(
                    query,
                    "❌ Провайдер Ollama не поддерживает список моделей.",
                    reply_markup=settings_menu(),
                )
                return
            
            models = provider.list_models()
            logger.info(f"Получен список моделей Ollama для изображений: {models}")
            
            if not models:
                logger.warning("Список моделей Ollama пуст")
                await safe_edit_message_text(
                    query,
                    "❌ Не удалось загрузить список моделей Ollama.\n\nПроверьте:\n1. Запущен ли Ollama сервер\n2. Правильно ли настроен OLLAMA_BASE_URL\n3. Есть ли модели в Ollama",
                    reply_markup=settings_menu(),
                )
                return
            
            current_model = preferred_image_model or (provider.model if hasattr(provider, 'model') else '')
            logger.info(f"Текущая модель для изображений: {current_model}")
            
            await safe_edit_message_text(
                query,
                f"🖼️ Выберите модель Ollama для обработки изображений:\n\nТекущая: {current_model or 'не выбрана'}",
                reply_markup=ollama_models_menu(models, current_model, target='image'),
            )
        except Exception as e:
            logger.error(f"Ошибка при получении списка моделей Ollama для изображений: {e}", exc_info=True)
            await safe_edit_message_text(
                query,
                f"❌ Ошибка при загрузке списка моделей: {str(e)}",
                reply_markup=settings_menu(),
            )
        return
    
    if data.startswith('ollama_model:'):
        # ВАЖНО: Callback_data парсим только через split(':', 1) + явную валидацию payload.
        # Формат: ollama_model:<target>:<model_name> или ollama_model:<target>:hash:<hash>
        _, payload = data.split(':', 1)  # payload = "text:llama3" или "text:hash:abcd1234"
        
        parts = payload.split(':')
        if len(parts) < 2:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return
        
        target = parts[0]
        
        # Если используется хеш, получаем модель из сохраненного списка
        if len(parts) >= 3 and parts[1] == 'hash':
            model_hash = parts[2]
            # Получаем список моделей из context
            models_key = 'ollama_models_text' if target == 'text' else 'ollama_models_image'
            models = context.user_data.get(models_key, [])
            
            if not models:
                await query.answer("Список моделей не найден. Пожалуйста, выберите модель заново.", show_alert=True)
                return
            
            # Находим модель по хешу
            import hashlib
            model_name = None
            for model in models:
                if hashlib.md5(model.encode()).hexdigest()[:8] == model_hash:
                    model_name = model
                    break
            
            if not model_name:
                await query.answer("Модель не найдена. Пожалуйста, выберите модель заново.", show_alert=True)
                return
        else:
            # model_name может содержать ':', поэтому объединяем все части после target
            model_name = ':'.join(parts[1:])

        if not model_name:
            await query.answer("Некорректное имя модели", show_alert=True)
            return

        session = Session()
        try:
            user_db = session.query(User).filter_by(telegram_id=user_id).first()
            if user_db:
                if target == 'image':
                    user_db.preferred_image_model = model_name
                    preferred_image_model = model_name  # Обновляем локальную переменную
                    message = f"✅ Модель для изображений изменена на {model_name}"
                else:
                    user_db.preferred_model = model_name
                    preferred_model = model_name  # Обновляем локальную переменную
                    message = f"✅ Модель для текста изменена на {model_name}"
                session.commit()
        finally:
            session.close()
        
        await safe_edit_message_text(query, message, reply_markup=settings_menu())
        return
    
    # Настройки RAG
    if data == 'rag_settings':
        from shared.config import RAG_MODEL_NAME, RAG_RERANK_MODEL
        text = (
            f"🔧 Настройки RAG\n\n"
            f"Текущая модель эмбеддингов: {RAG_MODEL_NAME}\n"
            f"Текущая модель ранкинга: {RAG_RERANK_MODEL}\n\n"
            f"ℹ️ Изменения сохраняются в .env файл.\n"
            f"🔄 После изменения модели используйте кнопку 'Перезагрузить модели' для применения без перезапуска бота."
        )
        await safe_edit_message_text(query, text, reply_markup=rag_settings_menu())
        return
    
    if data == 'select_embedding_model':
        try:
            import hashlib
            # Предустановленные модели эмбеддингов
            models = [
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                "intfloat/multilingual-e5-base",
                "intfloat/multilingual-e5-large",
                "sentence-transformers/all-MiniLM-L6-v2",
            ]
            from shared.config import RAG_MODEL_NAME
            current = RAG_MODEL_NAME
            
            # Сохраняем список моделей в context для восстановления по хешу
            context.user_data['rag_embedding_models'] = models
            
            # Telegram ограничивает callback_data до 64 байт
            # Формат: "rag_embedding_model:" + имя модели = минимум 22 символа
            # Значит на имя модели остается ~42 символа
            max_callback_length = 64
            prefix_length = len("rag_embedding_model:")
            max_model_name_length = max_callback_length - prefix_length - 5  # Запас
            
            buttons = []
            for model in models:
                prefix = "✅ " if model == current else "⚪ "
                # Обрезать длинные названия моделей для отображения
                display_name = model[:45] + "..." if len(model) > 45 else model
                
                # Если имя модели слишком длинное, используем хеш
                if len(model) > max_model_name_length:
                    model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
                    callback_data = f"rag_embedding_model:hash:{model_hash}"
                else:
                    callback_data = f"rag_embedding_model:{model}"
                
                # Проверяем длину на всякий случай
                if len(callback_data) > max_callback_length:
                    model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
                    callback_data = f"rag_embedding_model:hash:{model_hash}"
                
                buttons.append([InlineKeyboardButton(
                    f"{prefix}{display_name}",
                    callback_data=callback_data
                )])
            buttons.append([InlineKeyboardButton("🔙 К настройкам RAG", callback_data='rag_settings')])
            
            await safe_edit_message_text(
                query,
                f"📊 Выберите модель эмбеддингов:\n\nТекущая: {current}\n\nℹ️ Изменение сохранится в .env файл.\n⚠️ Требуется перезапуск бота для применения.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception as e:
            logger.error(f"Ошибка при загрузке списка моделей эмбеддингов: {e}", exc_info=True)
            await safe_edit_message_text(
                query,
                f"❌ Ошибка: {str(e)}",
                reply_markup=rag_settings_menu(),
            )
        return
    
    if data == 'select_rerank_model':
        try:
            import hashlib
            # Предустановленные модели ранкинга
            models = [
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "cross-encoder/ms-marco-MiniLM-L-12-v2",
                "BAAI/bge-reranker-base",
                "BAAI/bge-reranker-large",
            ]
            from shared.config import RAG_RERANK_MODEL
            current = RAG_RERANK_MODEL
            
            # Сохраняем список моделей в context для восстановления по хешу
            context.user_data['rag_rerank_models'] = models
            
            # Telegram ограничивает callback_data до 64 байт
            # Формат: "rag_rerank_model:" + имя модели = минимум 19 символов
            # Значит на имя модели остается ~45 символов
            max_callback_length = 64
            prefix_length = len("rag_rerank_model:")
            max_model_name_length = max_callback_length - prefix_length - 5  # Запас
            
            buttons = []
            for model in models:
                prefix = "✅ " if model == current else "⚪ "
                # Обрезать длинные названия моделей для отображения
                display_name = model[:45] + "..." if len(model) > 45 else model
                
                # Если имя модели слишком длинное, используем хеш
                if len(model) > max_model_name_length:
                    model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
                    callback_data = f"rag_rerank_model:hash:{model_hash}"
                else:
                    callback_data = f"rag_rerank_model:{model}"
                
                # Проверяем длину на всякий случай
                if len(callback_data) > max_callback_length:
                    model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
                    callback_data = f"rag_rerank_model:hash:{model_hash}"
                
                buttons.append([InlineKeyboardButton(
                    f"{prefix}{display_name}",
                    callback_data=callback_data
                )])
            buttons.append([InlineKeyboardButton("🔙 К настройкам RAG", callback_data='rag_settings')])
            
            await safe_edit_message_text(
                query,
                f"🎯 Выберите модель ранкинга:\n\nТекущая: {current}\n\nℹ️ Изменение сохранится в .env файл.\n⚠️ Требуется перезапуск бота для применения.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception as e:
            logger.error(f"Ошибка при загрузке списка моделей ранкинга: {e}", exc_info=True)
            await safe_edit_message_text(
                query,
                f"❌ Ошибка: {str(e)}",
                reply_markup=rag_settings_menu(),
            )
        return
    
    if data.startswith('rag_embedding_model:') or data.startswith('rag_rerank_model:'):
        import hashlib
        
        # ВАЖНО: Callback_data парсим только через split(':', 1) + явную валидацию payload.
        # Формат: rag_embedding_model:model_name или rag_embedding_model:hash:XXXXXXXX
        prefix, payload = data.split(':', 1)  # prefix = "rag_embedding_model", payload = "model_name" или "hash:XXXXXXXX"
        model_type = prefix
        
        # Проверяем, используется ли хеш
        if payload.startswith('hash:'):
            _, model_hash = payload.split(':', 1)
            # Получаем список моделей из context
            models_key = 'rag_embedding_models' if model_type == 'rag_embedding_model' else 'rag_rerank_models'
            models = context.user_data.get(models_key, [])
            
            if not models:
                await query.answer("Список моделей не найден. Пожалуйста, выберите модель заново.", show_alert=True)
                return
            
            # Находим модель по хешу
            model_name = None
            for model in models:
                if hashlib.md5(model.encode()).hexdigest()[:8] == model_hash:
                    model_name = model
                    break
            
            if not model_name:
                await query.answer("Модель не найдена. Пожалуйста, выберите модель заново.", show_alert=True)
                return
        else:
            # Прямое имя модели
            model_name = payload
        
        if not model_name:
            await query.answer("Некорректное имя модели", show_alert=True)
            return
        
        # Сохраняем в .env файл
        try:
            env_var_name = 'RAG_MODEL_NAME' if model_type == 'rag_embedding_model' else 'RAG_RERANK_MODEL'
            success = update_env_file(env_var_name, model_name)
            
            if success:
                if model_type == 'rag_embedding_model':
                    message = (
                        f"✅ Модель эмбеддингов изменена на {model_name}\n\n"
                        f"💾 Изменение сохранено в .env файл.\n\n"
                        f"🔄 Используйте кнопку 'Перезагрузить модели' в настройках RAG для применения без перезапуска бота."
                    )
                else:
                    message = (
                        f"✅ Модель ранкинга изменена на {model_name}\n\n"
                        f"💾 Изменение сохранено в .env файл.\n\n"
                        f"🔄 Используйте кнопку 'Перезагрузить модели' в настройках RAG для применения без перезапуска бота."
                    )
            else:
                message = (
                    f"✅ Модель изменена на {model_name}\n\n"
                    f"⚠️ Не удалось сохранить в .env файл. Изменения будут потеряны при перезапуске.\n\n"
                    f"🔄 Используйте кнопку 'Перезагрузить модели' для применения (или перезапустите бота)."
                )
        except Exception as e:
            logger.error(f"Ошибка при сохранении модели в .env: {e}", exc_info=True)
            if model_type == 'rag_embedding_model':
                message = (
                    f"✅ Модель эмбеддингов изменена на {model_name}\n\n"
                    f"⚠️ Ошибка сохранения в .env: {str(e)}\n\n"
                    f"🔄 Используйте кнопку 'Перезагрузить модели' для применения (или перезапустите бота)."
                )
            else:
                message = (
                    f"✅ Модель ранкинга изменена на {model_name}\n\n"
                    f"⚠️ Ошибка сохранения в .env: {str(e)}\n\n"
                    f"🔄 Используйте кнопку 'Перезагрузить модели' для применения (или перезапустите бота)."
                )
        
        await safe_edit_message_text(query, message, reply_markup=rag_settings_menu())
        return
    
    if data == 'rag_reload_models':
        # Перезагрузить модели RAG в рантайме через backend
        try:
            await safe_edit_message_text(query, "🔄 Перезагрузка моделей RAG...\n\nЭто может занять некоторое время.")

            result = await asyncio.to_thread(backend_client.rag_reload_models)
            embedding_ok = bool(result.get("embedding"))
            reranker_ok = bool(result.get("reranker"))

            if embedding_ok and reranker_ok:
                message = (
                    "✅ Модели RAG успешно перезагружены!\n\n"
                    "• Модель эмбеддингов: перезагружена\n"
                    "• Модель ранкинга: перезагружена\n\n"
                    "Изменения применены без перезапуска бота."
                )
            elif embedding_ok:
                message = (
                    "⚠️ Частичная перезагрузка моделей RAG:\n\n"
                    "• Модель эмбеддингов: ✅ перезагружена\n"
                    "• Модель ранкинга: ❌ ошибка перезагрузки\n\n"
                    "Проверьте логи для деталей."
                )
            elif reranker_ok:
                message = (
                    "⚠️ Частичная перезагрузка моделей RAG:\n\n"
                    "• Модель эмбеддингов: ❌ ошибка перезагрузки\n"
                    "• Модель ранкинга: ✅ перезагружена\n\n"
                    "Проверьте логи для деталей."
                )
            else:
                message = (
                    "❌ Ошибка перезагрузки моделей RAG:\n\n"
                    "• Модель эмбеддингов: ❌ ошибка\n"
                    "• Модель ранкинга: ❌ ошибка\n\n"
                    "Проверьте логи для деталей. Возможно, требуется перезапуск бота."
                )

            await safe_edit_message_text(query, message, reply_markup=rag_settings_menu())
        except Exception as e:
            logger.error(f"Ошибка при перезагрузке моделей RAG через backend: {e}", exc_info=True)
            await safe_edit_message_text(
                query,
                f"❌ Ошибка перезагрузки моделей: {str(e)}\n\nПроверьте логи для деталей.",
                reply_markup=rag_settings_menu(),
            )
        return
    
    # Поиск в базе знаний
    if data == 'search_kb':
        context.user_data['state'] = 'waiting_query'
        await safe_edit_message_text(query, "🔍 Введите запрос для поиска в базе знаний:")
        return

    if data == 'search_options':
        await safe_edit_message_text(query, "🔎 Выберите вариант поиска:", reply_markup=search_options_menu())
        return

    if data == 'search_summary':
        await safe_edit_message_text(query, "📝 Выберите режим сводки:", reply_markup=summary_mode_menu())
        return

    if data == 'summary_date_range':
        context.user_data["state"] = "waiting_summary_date_from"
        await safe_edit_message_text(query, "Введите дату ОТ (YYYY-MM-DD) или '-' для пропуска:")
        return

    if data == 'summary_last_chat':
        await safe_edit_message_text(query, "🕒 Ищу последний импорт чата...")
        # Найти последнюю запись chat в журналах всех KB
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        last_item = None
        for kb in kbs or []:
            kb_id = getattr(kb, "id", None) or kb.get("id")
            try:
                logs = await asyncio.to_thread(backend_client.get_import_log, kb_id)
            except Exception:
                logs = []
            for item in logs or []:
                if item.get("action_type") != "chat":
                    continue
                ts = item.get("created_at") or item.get("timestamp") or ""
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    dt = None
                last_dt = last_item.get("_dt") if last_item else None
                if not last_item or (dt and (last_dt is None or dt > last_dt)):
                    last_item = {**item, "kb_id": kb_id, "_dt": dt}
        if not last_item:
            await safe_edit_message_text(query, "❌ Не найден импорт чатов.")
            return
        kb_id = last_item.get("kb_id")
        mode = context.user_data.get("summary_mode", "summary")
        from frontend.bot_handlers import perform_rag_summary_and_render
        answer_html, has_answer = await perform_rag_summary_and_render(
            "Сделай сводку по последнему чату",
            kb_id,
            mode,
            date_from=context.user_data.get("summary_filters", {}).get("date_from"),
            date_to=context.user_data.get("summary_filters", {}).get("date_to"),
        )
        if has_answer:
            await safe_edit_message_text(query, answer_html, parse_mode='HTML')
        else:
            await safe_edit_message_text(query, "❌ Не удалось сформировать сводку.")
        return

    if data.startswith("summary_mode:"):
        mode = data.split(":", 1)[1]
        context.user_data["summary_mode"] = mode
        context.user_data["state"] = "waiting_summary_query"
        await safe_edit_message_text(query, "Введите запрос для сводки/FAQ/инструкции:")
        return

    if data == 'search_filters':
        await safe_edit_message_text(
            query,
            "⚙️ Настройка фильтров поиска в БЗ:",
            reply_markup=search_filters_menu(context.user_data.get("rag_filters")),
        )
        return

    if data.startswith("search_filter:"):
        action = data.split(":", 1)[1]
        filters = context.user_data.get("rag_filters") or {}
        if action == "toggle_type":
            current = filters.get("source_types") or []
            if not current:
                filters["source_types"] = ["markdown", "pdf", "word", "text", "web", "excel"]
            elif "code" not in current:
                filters["source_types"] = ["code"]
            else:
                filters["source_types"] = []
        elif action == "toggle_lang":
            current = (filters.get("languages") or [])
            if not current:
                filters["languages"] = ["ru"]
            elif "ru" in current:
                filters["languages"] = ["en"]
            else:
                filters["languages"] = []
        elif action == "set_path":
            context.user_data["state"] = "waiting_filter_path"
            await safe_edit_message_text(query, "Введите префикс пути (например: src/ или docs/). Для очистки отправьте '-'")
            return
        elif action == "clear":
            filters = {}
        context.user_data["rag_filters"] = filters
        await safe_edit_message_text(
            query,
            "⚙️ Настройка фильтров поиска в БЗ:",
            reply_markup=search_filters_menu(filters),
        )
        return
    
    # Поиск в интернете
    if data == 'search_web':
        context.user_data['state'] = 'waiting_web_query'
        await safe_edit_message_text(query, "🌐 Введите запрос для поиска в интернете:")
        return
    
    # Задать вопрос ИИ
    if data == 'ask_ai':
        context.user_data['state'] = 'waiting_ai_query'
        await safe_edit_message_text(query, "🤖 Задайте вопрос ИИ:")
        return
    
    # Обработка изображения
    if data == 'process_image':
        await safe_edit_message_text(query, "🖼️ Отправьте изображение для обработки")
        return
    
    # Админ-меню
    if role == 'admin':
        # Передаём данные пользователя как dict для совместимости
        user_data = {
            "telegram_id": user_id,
            "username": username,
            "role": role,
            "preferred_provider": preferred_provider,
            "preferred_model": preferred_model,
            "preferred_image_model": preferred_image_model,
        }
        await handle_admin_callbacks(query, context, data, user_data)
    else:
        await query.answer("У вас нет прав администратора", show_alert=True)


def _build_users_page_keyboard(users, page: int, page_size: int = 5) -> InlineKeyboardMarkup:
    """Сформировать inline-клавиатуру для списка пользователей с пагинацией.

    users может быть списком ORM-объектов User или dict'ов из backend API.

    Для каждого пользователя рисуем ОТДЕЛЬНУЮ строку кнопок,
    причём в тексте кнопок явно указываем номер и имя/логин пользователя,
    чтобы было видно, какая пара кнопок относится к какому пользователю.

      1) Кнопка «одобрить/сменить роль»
      2) Кнопка «удалить»
    """
    total = len(users)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    end = start + page_size
    page_users = users[start:end]

    buttons: list[list[InlineKeyboardButton]] = []

    for local_idx, u in enumerate(page_users, start=1):
        # Глобальный номер пользователя на странице (совпадает с нумерацией в тексте)
        number = start + local_idx

        # Унифицированный доступ к полям пользователя
        user_id = getattr(u, "id", None) or u.get("id")
        approved = getattr(u, "approved", None)
        if approved is None:
            approved = bool(u.get("approved"))
        role = getattr(u, "role", None) or u.get("role") or "user"

        # Человекочитаемое имя для подписи на кнопках
        full_name = (
            u.get("full_name") if isinstance(u, dict) else getattr(u, "full_name", None)
        ) or ""
        username_raw = (
            u.get("username") if isinstance(u, dict) else getattr(u, "username", None)
        )
        username = f"@{username_raw}" if username_raw else ""
        telegram_id = (
            u.get("telegram_id") if isinstance(u, dict) else getattr(u, "telegram_id", "")
        )

        if full_name:
            user_label = full_name
        elif username:
            user_label = username
        elif telegram_id:
            user_label = f"id:{telegram_id}"
        else:
            user_label = f"id:{user_id}"

        prefix = f"{number}. "

        # Определяем подпись для кнопки смены роли / акцепта
        if not approved:
            toggle_label = f"{prefix}✅ Одобрить ({user_label})"
        else:
            if (role or "user") == "admin":
                toggle_label = f"{prefix}🔁 admin → user ({user_label})"
            else:
                toggle_label = f"{prefix}🔁 user → admin ({user_label})"

        delete_label = f"{prefix}🗑️ Удалить ({user_label})"

        buttons.append(
            [
                InlineKeyboardButton(
                    toggle_label,
                    callback_data=f"user_toggle:{user_id}:{page}",
                ),
                InlineKeyboardButton(
                    delete_label,
                    callback_data=f"user_delete:{user_id}:{page}",
                ),
            ]
        )

    # Пагинация
    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_users_page:{page-1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Вперёд ➡️", callback_data=f"admin_users_page:{page+1}")
        )
    if nav_row:
        buttons.append(nav_row)

    # Кнопка назад в админ-меню
    buttons.append([InlineKeyboardButton("🔙 Админ-меню", callback_data="admin_menu")])

    return InlineKeyboardMarkup(buttons)


async def handle_admin_callbacks(query, context, data: str, user: dict):
    """Обработка админских callback'ов
    
    ВАЖНО: user - это dict с данными пользователя, не ORM объект.
    Права пользователя/approved берём только из backend, локальная БД — кэш.
    """
    
    # Админ-меню
    if data == 'admin_menu':
        await safe_edit_message_text(query, "👨‍💼 Админ-панель:", reply_markup=admin_menu())
        return
    
    # Управление пользователями
    if data == 'admin_users':
        # Показать первую страницу списка пользователей (через backend)
        users = await asyncio.to_thread(backend_client.list_users)
        from html import escape

        if not users:
            await safe_edit_message_text(
                query,
                "👥 Пользователей пока нет.",
                reply_markup=user_management_menu(),
            )
            return

        page = 1
        keyboard = _build_users_page_keyboard(users, page)

        lines = [f"👥 <b>Управление пользователями</b> (стр. {page})", ""]
        for idx, u in enumerate(users[:5], start=1):
            full_name = (u.get("full_name") if isinstance(u, dict) else getattr(u, "full_name", None)) or "-"
            username_raw = u.get("username") if isinstance(u, dict) else getattr(u, "username", None)
            username = f"@{username_raw}" if username_raw else "-"
            phone = (u.get("phone") if isinstance(u, dict) else getattr(u, "phone", None)) or "не указан"
            approved = u.get("approved") if isinstance(u, dict) else getattr(u, "approved", False)
            role = (u.get("role") if isinstance(u, dict) else getattr(u, "role", None)) or "user"
            status = "✅ одобрен" if approved else "⏳ заявка"
            telegram_id = u.get("telegram_id") if isinstance(u, dict) else getattr(u, "telegram_id", "")

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(str(telegram_id))}</code>\n"
                f"   Телефон: {escape(phone)}\n"
                f"   Роль: {escape(role)}, Статус: {status}\n"
            )

        text = "\n".join(lines)
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data.startswith("admin_users_page:"):
        try:
            page = int(data.split(":")[1])
        except (ValueError, IndexError):
            page = 1
        users = await asyncio.to_thread(backend_client.list_users)
        from html import escape

        if not users:
            await safe_edit_message_text(
                query,
                "👥 Пользователей пока нет.",
                reply_markup=user_management_menu(),
            )
            return

        keyboard = _build_users_page_keyboard(users, page)
        page_size = 5
        total_pages = max(1, (len(users) + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        end = start + page_size
        page_users = users[start:end]

        lines = [f"👥 <b>Управление пользователями</b> (стр. {page}/{total_pages})", ""]
        for idx, u in enumerate(page_users, start=1 + start):
            full_name = (u.get("full_name") if isinstance(u, dict) else getattr(u, "full_name", None)) or "-"
            username_raw = u.get("username") if isinstance(u, dict) else getattr(u, "username", None)
            username = f"@{username_raw}" if username_raw else "-"
            phone = (u.get("phone") if isinstance(u, dict) else getattr(u, "phone", None)) or "не указан"
            approved = u.get("approved") if isinstance(u, dict) else getattr(u, "approved", False)
            role = (u.get("role") if isinstance(u, dict) else getattr(u, "role", None)) or "user"
            status = "✅ одобрен" if approved else "⏳ заявка"
            telegram_id = u.get("telegram_id") if isinstance(u, dict) else getattr(u, "telegram_id", "")

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(str(telegram_id))}</code>\n"
                f"   Телефон: {escape(phone)}\n"
                f"   Роль: {escape(role)}, Статус: {status}\n"
            )

        text = "\n".join(lines)
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data.startswith("user_toggle:"):
        # Формат: user_toggle:<user_db_id>:<page>
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("Некорректные данные пользователя", show_alert=True)
            return
        try:
            target_id = int(parts[1])
            page = int(parts[2])
        except ValueError:
            await query.answer("Некорректный идентификатор пользователя", show_alert=True)
            return

        ok = await asyncio.to_thread(backend_client.toggle_user_role, target_id)
        if not ok:
            await query.answer("Не удалось изменить роль пользователя (backend)", show_alert=True)
            return

        # Перерисуем текущую страницу
        users = await asyncio.to_thread(backend_client.list_users)
        from html import escape

        keyboard = _build_users_page_keyboard(users, page)
        page_size = 5
        total_pages = max(1, (len(users) + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        end = start + page_size
        page_users = users[start:end]

        lines = [f"👥 <b>Управление пользователями</b> (стр. {page}/{total_pages})", ""]
        for idx, u in enumerate(page_users, start=1 + start):
            full_name = (u.get("full_name") if isinstance(u, dict) else getattr(u, "full_name", None)) or "-"
            username_raw = u.get("username") if isinstance(u, dict) else getattr(u, "username", None)
            username = f"@{username_raw}" if username_raw else "-"
            phone = (u.get("phone") if isinstance(u, dict) else getattr(u, "phone", None)) or "не указан"
            approved = u.get("approved") if isinstance(u, dict) else getattr(u, "approved", False)
            role = (u.get("role") if isinstance(u, dict) else getattr(u, "role", None)) or "user"
            status = "✅ одобрен" if approved else "⏳ заявка"
            telegram_id = u.get("telegram_id") if isinstance(u, dict) else getattr(u, "telegram_id", "")

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(str(telegram_id))}</code>\n"
                f"   Телефон: {escape(phone)}\n"
                f"   Роль: {escape(role)}, Статус: {status}\n"
            )

        text = "\n".join(lines)
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data.startswith("user_delete:"):
        # Формат: user_delete:<user_db_id>:<page>
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("Некорректные данные пользователя", show_alert=True)
            return
        try:
            target_id = int(parts[1])
            page = int(parts[2])
        except ValueError:
            await query.answer("Некорректный идентификатор пользователя", show_alert=True)
            return

        ok = await asyncio.to_thread(backend_client.delete_user, target_id)
        if not ok:
            await query.answer("Не удалось удалить пользователя (backend)", show_alert=True)
            return

        users = await asyncio.to_thread(backend_client.list_users)
        from html import escape

        if not users:
            await safe_edit_message_text(
                query,
                "👥 Пользователей больше нет.",
                reply_markup=user_management_menu(),
            )
            return

        keyboard = _build_users_page_keyboard(users, page)
        page_size = 5
        total_pages = max(1, (len(users) + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        end = start + page_size
        page_users = users[start:end]

        lines = [f"👥 <b>Управление пользователями</b> (стр. {page}/{total_pages})", ""]
        for idx, u in enumerate(page_users, start=1 + start):
            full_name = (u.get("full_name") if isinstance(u, dict) else getattr(u, "full_name", None)) or "-"
            username_raw = u.get("username") if isinstance(u, dict) else getattr(u, "username", None)
            username = f"@{username_raw}" if username_raw else "-"
            phone = (u.get("phone") if isinstance(u, dict) else getattr(u, "phone", None)) or "не указан"
            approved = u.get("approved") if isinstance(u, dict) else getattr(u, "approved", False)
            role = (u.get("role") if isinstance(u, dict) else getattr(u, "role", None)) or "user"
            status = "✅ одобрен" if approved else "⏳ заявка"
            telegram_id = u.get("telegram_id") if isinstance(u, dict) else getattr(u, "telegram_id", "")

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(str(telegram_id))}</code>\n"
                f"   Телефон: {escape(phone)}\n"
                f"   Роль: {escape(role)}, Статус: {status}\n"
            )

        text = "\n".join(lines)
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="HTML")
        return
    
    # Управление базами знаний
    if data == 'admin_kb':
        # Теперь список баз знаний получаем из backend-сервиса
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        await safe_edit_message_text(query, "📚 Базы знаний:", reply_markup=knowledge_base_menu(kbs))
        return
    
    if data == 'kb_create':
        context.user_data['state'] = 'waiting_kb_name'
        await safe_edit_message_text(query, "Введите название новой базы знаний:")
        return
    
    if data.startswith('kb_select:'):
        kb_id = int(data.split(':')[1])
        # Получаем список баз знаний и ищем нужную
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        kb = next((item for item in kbs if int(item.get("id")) == kb_id), None) if kbs else None
        if kb:
            # Получить количество фрагментов через список источников
            try:
                sources = await asyncio.to_thread(backend_client.list_knowledge_sources, kb_id) or []
                chunks_count = sum(int(src.get("chunks_count", 0)) for src in sources)
            except Exception:
                chunks_count = 0

            name = kb.get("name") or "Без названия"
            description = kb.get("description") or "Нет описания"
            text = f"📚 База знаний: {name}\n\nОписание: {description}\nФрагментов: {chunks_count}"
            
            # Проверить, есть ли ожидающий документ для загрузки
            if 'pending_document' in context.user_data:
                # Установить базу знаний и загрузить документ
                context.user_data['kb_id'] = kb_id
                pending = context.user_data.pop('pending_document')
                
                # Загрузить документ асинхронно через backend
                from frontend.bot_handlers import load_document_to_kb
                await safe_edit_message_text(query, "📤 Загружаю документ...")
                await load_document_to_kb(query, context, pending, kb_id)
                return
            
            # Проверить, есть ли ожидающий запрос для поиска
            if context.user_data.get('state') == 'waiting_kb_for_query' and 'pending_query' in context.user_data:
                # Установить базу знаний и выполнить запрос напрямую
                context.user_data['kb_id'] = kb_id
                pending_query = context.user_data.pop('pending_query')
                context.user_data['state'] = None
                
                await safe_edit_message_text(query, f"🔍 Ищу в базе знаний '{name}'...")
                
                from shared.utils import strip_html_tags
                from frontend.templates.buttons import main_menu
                from frontend.bot_handlers import perform_rag_query_and_render
                
                tg_id = str(query.from_user.id) if query.from_user else ""
                username = query.from_user.username if query.from_user else None
                full_name = getattr(query.from_user, "full_name", None) if query.from_user else None
                
                # Берём source-of-truth из backend_user (уже получен в callback_handler),
                # а preferred_* — из локального кэша (preferred_model/provider переменные выше по стеку)
                # Здесь user - dict, который мы передали в handle_admin_callbacks
                user_for_rag = UserContext(
                    telegram_id=tg_id,
                    username=username,
                    full_name=full_name,
                    role=(user.get("role") or "user"),
                    approved=True,
                    preferred_provider=user.get("preferred_provider"),
                    preferred_model=user.get("preferred_model"),
                    preferred_image_model=user.get("preferred_image_model"),
                )
                
                answer_html, has_answer = await perform_rag_query_and_render(
                    pending_query, kb_id, user_for_rag
                )
                
                menu = main_menu(is_admin=(user_for_rag.role == 'admin') if user_for_rag else False)
                try:
                    await safe_edit_message_text(query, answer_html, reply_markup=menu, parse_mode='HTML')
                except Exception as e:
                    logger.warning("Ошибка форматирования HTML, отправляю plain текст: %s", e)
                    answer_plain = strip_html_tags(answer_html)
                    await safe_edit_message_text(query, answer_plain, reply_markup=menu, parse_mode=None)
                return

            if context.user_data.get('state') == 'waiting_kb_for_query' and 'pending_summary_query' in context.user_data:
                context.user_data['kb_id'] = kb_id
                pending_query = context.user_data.pop('pending_summary_query')
                context.user_data['state'] = None
                mode = context.user_data.get("summary_mode", "summary")

                await safe_edit_message_text(query, f"📝 Формирую сводку по базе '{name}'...")

                from shared.utils import strip_html_tags
                from frontend.templates.buttons import main_menu
                from frontend.bot_handlers import perform_rag_summary_and_render

                summary_filters = context.user_data.get("summary_filters") or {}
                answer_html, has_answer = await perform_rag_summary_and_render(
                    pending_query,
                    kb_id,
                    mode,
                    date_from=summary_filters.get("date_from"),
                    date_to=summary_filters.get("date_to"),
                )

                menu = main_menu(is_admin=(user.get("role") == 'admin'))
                try:
                    await safe_edit_message_text(query, answer_html, reply_markup=menu, parse_mode='HTML')
                except Exception as e:
                    logger.warning("Ошибка форматирования HTML, отправляю plain текст: %s", e)
                    answer_plain = strip_html_tags(answer_html)
                    await safe_edit_message_text(query, answer_plain, reply_markup=menu, parse_mode=None)
                return
            
            await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
        return
    
    if data.startswith('ingest_chunking:'):
        parts = data.split(':', 3)
        if len(parts) < 4:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return
        kb_id = int(parts[1])
        source_kind = parts[2]
        mode = parts[3]

        pending = context.user_data.get("pending_ingest") or {}
        if pending.get("kb_id") != kb_id or pending.get("kind") != source_kind:
            await safe_edit_message_text(query, "?? Нет данных для продолжения загрузки.", reply_markup=kb_actions_menu(kb_id))
            return

        settings_resp = await asyncio.to_thread(backend_client.get_kb_settings, kb_id)
        settings = settings_resp.get("settings") if isinstance(settings_resp, dict) else {}
        if not isinstance(settings, dict):
            settings = {}

        def set_nested(d: dict, path: str, val: object) -> None:
            parts_path = path.split(".")
            cur = d
            for p in parts_path[:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    cur[p] = {}
                cur = cur[p]
            cur[parts_path[-1]] = val

        set_nested(settings, f"chunking.{source_kind}.mode", mode)
        await asyncio.to_thread(backend_client.update_kb_settings, kb_id, settings)

        url = pending.get("url") or ""
        tg_id = str(query.from_user.id) if query.from_user else ""
        username = query.from_user.username if query.from_user else ""

        if source_kind == "web":
            await safe_edit_message_text(query, "?? Загружаю веб-страницу...")
            stats = await asyncio.to_thread(
                backend_client.ingest_web_page,
                kb_id=kb_id,
                url=url,
                telegram_id=tg_id or None,
                username=username or None,
            )
            chunks_added = int(stats.get("chunks_added", 0)) if stats else 0
            text = f"? Загружено {chunks_added} фрагментов с веб-страницы."
            await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
        elif source_kind == "wiki":
            await safe_edit_message_text(query, "?? Запускаю рекурсивный обход вики...")
            stats = await asyncio.to_thread(
                backend_client.ingest_wiki_crawl,
                kb_id=kb_id,
                url=url,
                telegram_id=tg_id or None,
                username=username or None,
            )
            deleted = stats.get("deleted_chunks", 0)
            pages = stats.get("pages_processed", 0) or 0
            added = stats.get("chunks_added", 0)
            wiki_root = stats.get("wiki_root", url)
            text = (
                "? Сканирование вики завершено.\n\n"
                f"Исходный URL: {url}\n"
                f"Корневой wiki-URL: {wiki_root}\n"
                f"Удалено старых фрагментов: {deleted}\n"
                f"Обработано страниц: {pages}\n"
                f"Добавлено фрагментов: {added}"
            )
            await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
        else:
            await safe_edit_message_text(query, "?? Неизвестный тип загрузки.", reply_markup=kb_actions_menu(kb_id))

        context.user_data.pop("pending_ingest", None)
        return

    if data.startswith('kb_settings:'):
        kb_id = int(data.split(':')[1])
        settings_resp = await asyncio.to_thread(backend_client.get_kb_settings, kb_id)
        settings = settings_resp.get("settings") if isinstance(settings_resp, dict) else None
        if not settings:
            await safe_edit_message_text(query, "?? Не удалось загрузить настройки KB.", reply_markup=kb_actions_menu(kb_id))
            return
        text = (
            f"?? Настройки базы знаний #{kb_id}\n\n"
            "Нажимайте на кнопки, чтобы переключить режим."
        )
        await safe_edit_message_text(query, text, reply_markup=kb_settings_menu(kb_id, settings))
        return

    if data.startswith('kb_setting:'):
        parts = data.split(':', 3)
        if len(parts) < 4:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return
        kb_id = int(parts[1])
        key_path = parts[2]
        value = parts[3]

        settings_resp = await asyncio.to_thread(backend_client.get_kb_settings, kb_id)
        settings = settings_resp.get("settings") if isinstance(settings_resp, dict) else {}
        if not isinstance(settings, dict):
            settings = {}

        def set_nested(d: dict, path: str, val: object) -> None:
            parts_path = path.split(".")
            cur = d
            for p in parts_path[:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    cur[p] = {}
                cur = cur[p]
            cur[parts_path[-1]] = val

        if value.lower() in ("true", "false"):
            cast_val: object = value.lower() == "true"
        else:
            cast_val = value

        set_nested(settings, key_path, cast_val)
        updated = await asyncio.to_thread(backend_client.update_kb_settings, kb_id, settings)
        updated_settings = updated.get("settings") if isinstance(updated, dict) else settings

        await safe_edit_message_text(
            query,
            f"?? Настройки обновлены для KB #{kb_id}.",
            reply_markup=kb_settings_menu(kb_id, updated_settings),
        )
        return

    if data.startswith('kb_code:'):
        kb_id = int(data.split(':')[1])
        buttons = [
            [InlineKeyboardButton("?? Локальный путь", callback_data=f"kb_code_path:{kb_id}")],
            [InlineKeyboardButton("?? Git URL", callback_data=f"kb_code_git:{kb_id}")],
            [InlineKeyboardButton("?? Назад", callback_data=f"kb_select:{kb_id}")],
        ]
        await safe_edit_message_text(
            query,
            "Выберите источник кода для индексирования:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith('kb_code_path:'):
        kb_id = int(data.split(':')[1])
        context.user_data['kb_id_for_code'] = kb_id
        context.user_data['state'] = 'waiting_code_path'
        await safe_edit_message_text(
            query,
            "Введите путь к локальной папке с кодом (путь должен быть доступен контейнеру).",
        )
        return

    if data.startswith('kb_code_git:'):
        kb_id = int(data.split(':')[1])
        context.user_data['kb_id_for_code'] = kb_id
        context.user_data['state'] = 'waiting_code_git'
        await safe_edit_message_text(
            query,
            "Введите URL git-репозитория для индексирования кода.",
        )
        return

    if data.startswith('kb_upload:'):
        kb_id = int(data.split(':')[1])
        context.user_data['kb_id'] = kb_id
        context.user_data['upload_mode'] = 'document'
        await safe_edit_message_text(query, "Выберите тип документа для загрузки:", reply_markup=document_type_menu())
        return
    
    if data.startswith('kb_wiki_crawl:'):
        kb_id = int(data.split(':')[1])
        context.user_data['kb_id_for_wiki'] = kb_id
        context.user_data['state'] = 'waiting_wiki_root'
        await safe_edit_message_text(
            query,
            "Введите корневой URL вики (например, https://gitee.com/mazurdenis/open-harmony/wikis).\n"
            "Бот рекурсивно обойдёт только страницы в этом разделе и загрузит их в выбранную базу знаний."
        )
        return
    
    if data.startswith('wiki_git_load:'):
        # Формат: wiki_git_load:kb_id:wiki_url_hash
        parts = data.split(':', 2)
        if len(parts) < 3:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return

        kb_id = int(parts[1])
        wiki_url_hash = parts[2]
        # Получаем полный URL из context.user_data
        wiki_url = context.user_data.get('wiki_urls', {}).get(wiki_url_hash)
        if not wiki_url:
            await query.answer("URL вики не найден. Попробуйте загрузить вики снова.", show_alert=True)
            return

        await safe_edit_message_text(
            query,
            "🔄 Загрузка вики через git-репозиторий...\n\n"
            "Это может занять несколько минут в зависимости от размера репозитория."
        )

        try:
            tg_id = str(query.from_user.id) if query.from_user else ""
            username = query.from_user.username if query.from_user else ""

            stats = backend_client.ingest_wiki_git(
                kb_id=kb_id,
                url=wiki_url,
                telegram_id=tg_id or None,
                username=username or None,
            )
            deleted = stats.get("deleted_chunks", 0)
            files = stats.get("files_processed", 0)
            added = stats.get("chunks_added", 0)
            wiki_root = stats.get("wiki_root", wiki_url)

            text = (
                "✅ Загрузка вики через git завершена.\n\n"
                f"Исходный URL: {wiki_url}\n"
                f"Корневой wiki-URL: {wiki_root}\n"
                f"Удалено старых фрагментов: {deleted}\n"
                f"Обработано файлов: {files}\n"
                f"Добавлено фрагментов: {added}"
            )
            await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
        except Exception as e:
            logger.error(f"Ошибка при загрузке вики через git (backend): {e}", exc_info=True)
            await safe_edit_message_text(
                query,
                f"❌ Ошибка при загрузке вики через git: {str(e)}\n\n"
                "Убедитесь, что:\n"
                "• Git установлен в системе\n"
                "• Репозиторий доступен для клонирования\n"
                "• URL вики корректный",
                reply_markup=kb_actions_menu(kb_id),
            )
        return
    
    if data.startswith('wiki_zip_load:'):
        # Формат: wiki_zip_load:kb_id:wiki_url_hash
        parts = data.split(':', 2)
        if len(parts) < 3:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return
        
        kb_id = int(parts[1])
        wiki_url_hash = parts[2]
        # Получаем полный URL из context.user_data
        wiki_url = context.user_data.get('wiki_urls', {}).get(wiki_url_hash)
        if not wiki_url:
            await query.answer("URL вики не найден. Попробуйте загрузить вики снова.", show_alert=True)
            return
        
        # Сохранить информацию для последующей обработки ZIP файла
        context.user_data['wiki_zip_kb_id'] = kb_id
        context.user_data['wiki_zip_url'] = wiki_url
        context.user_data['state'] = 'waiting_wiki_zip'
        
        await safe_edit_message_text(
            query,
            f"📦 Загрузка вики из ZIP архива\n\n"
            f"URL вики: {wiki_url}\n"
            f"База знаний: {kb_id}\n\n"
            "Отправьте ZIP архив с файлами вики. Бот автоматически:\n"
            "• Извлечет все markdown файлы из архива\n"
            "• Восстановит ссылки на оригинальные страницы вики\n"
            "• Добавит их в базу знаний"
        )
        return
    
    if data.startswith('kb_import_log:'):
        kb_id = int(data.split(':')[1])
        logs = backend_client.get_import_log(kb_id)
        if not logs:
            text = "Журнал загрузок пуст для этой базы знаний."
        else:
            from html import escape

            lines = ["📜 <b>Журнал последних загрузок:</b>\n"]
            max_logs = 50  # Ограничиваем количество записей
            for log in logs[:max_logs]:
                when = str(log.get("created_at") or "")[:16]
                username = log.get("username") or ""
                user_telegram_id = log.get("user_telegram_id") or ""
                who = username or user_telegram_id or "?"
                action_type = log.get("action_type") or ""
                source_path = log.get("source_path") or ""
                # Обрезаем длинные пути
                if len(source_path) > 60:
                    source_path = source_path[:57] + "..."
                total_chunks = int(log.get("total_chunks") or 0)

                lines.append(
                    f"- {escape(when)} — {escape(str(who))} — "
                    f"{escape(action_type)} — {escape(source_path)} "
                    f"(фрагментов: {total_chunks})"
                )
            
            if len(logs) > max_logs:
                lines.append(f"\n<i>... и ещё {len(logs) - max_logs} записей</i>")
            
            full_text = "\n".join(lines)
            # Обрезаем текст если он слишком длинный
            max_len = 3900
            if len(full_text) > max_len:
                new_lines: list[str] = []
                for line in lines:
                    candidate = "\n".join(new_lines + [line]) if new_lines else line
                    if len(candidate) > max_len:
                        break
                    new_lines.append(line)
                if new_lines:
                    new_lines.append("\n<i>... (текст обрезан из-за ограничения Telegram)</i>")
                text = "\n".join(new_lines)
            else:
                text = full_text
        await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id), parse_mode='HTML')
        return
    
    if data.startswith('kb_sources:'):
        parts = data.split(':')
        kb_id = int(parts[1])
        # Поддержка пагинации: kb_sources:<kb_id>:<page>
        try:
            page = int(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            page = 1

        page_size = 15  # Кол-во источников на страницу

        # Теперь источники берём из backend-сервиса
        from urllib.parse import unquote
        from html import escape
        from shared.utils import normalize_wiki_url_for_display

        sources_list = await asyncio.to_thread(backend_client.list_knowledge_sources, kb_id)
        total_sources = len(sources_list)
        logger.info("[kb_sources] Получено %s источников из backend для kb_id=%s", total_sources, kb_id)

        if total_sources == 0:
            text = "В этой базе знаний нет загруженных источников."
        else:
            # Пагинация по источникам
            total_pages = max(1, (total_sources + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_sources = sources_list[start_idx:end_idx]

            lines = [f"📋 <b>Список источников в базе знаний</b> (стр. {page}/{total_pages}):\n"]
            displayed_count = 0
            for source_data in page_sources:
                source_path = source_data.get("source_path") or ""
                source_type = source_data.get("source_type") or "unknown"
                last_updated = source_data.get("last_updated")
                chunks_count = int(source_data.get("chunks_count") or 0)

                if ".keep" in (source_path or "").lower():
                    logger.debug("[kb_sources] Пропущен источник с .keep: %s", source_path)
                    continue

                displayed_count += 1

                # Формируем отображаемое имя и ссылку
                is_url = source_type == "web" or (
                    source_path and source_path.startswith(("http://", "https://"))
                )

                if is_url and source_path:
                    url_for_link = source_path
                    # Нормализуем URL для вики (для отображения)
                    display_path = normalize_wiki_url_for_display(source_path)

                    # Извлекаем название из пути для отображения
                    if "/" in url_for_link:
                        parts = [p for p in url_for_link.split("/") if p]
                        if parts:
                            title = parts[-1]
                        else:
                            title = url_for_link
                    else:
                        title = url_for_link

                    # Декодируем URL для читаемости
                    title = unquote(title)

                    # Если title слишком короткий, берем предпоследнюю часть
                    if not title or len(title) < 2:
                        parts = [p for p in url_for_link.split("/") if p]
                        if len(parts) > 1:
                            title = unquote(parts[-2])
                        else:
                            title = url_for_link

                    title_escaped = escape(title)
                    url_escaped = escape(url_for_link)
                    path_display = f'<a href="{url_escaped}">{title_escaped}</a>'
                elif "::" in (source_path or ""):
                    file_name = source_path.split("::")[-1]
                    file_name = unquote(file_name) if "%" in file_name else file_name
                    path_display = f"<code>{escape(file_name)}</code>"
                elif "/" in (source_path or ""):
                    file_name = source_path.split("/")[-1]
                    file_name = unquote(file_name) if "%" in file_name else file_name
                    path_display = f"<code>{escape(file_name)}</code>"
                else:
                    path_to_display = (
                        unquote(source_path) if source_path and "%" in source_path else (source_path or "не указан")
                    )
                    path_display = escape(path_to_display)

                date_str = str(last_updated)[:16] if last_updated else "?"
                lines.append(f"• {path_display}")
                lines.append(f"  Тип: {source_type}, фрагментов: {chunks_count}, обновлено: {date_str}\n")

            # Собираем текст и при необходимости обрезаем по целым строкам
            full_text = "\n".join(lines)
            logger.info(
                "[kb_sources] Отображается %s источников из %s (страница %s)",
                displayed_count,
                total_sources,
                page,
            )

            max_len = 3900
            if len(full_text) <= max_len:
                text = full_text
            else:
                new_lines: list[str] = []
                for line in lines:
                    candidate = "\n".join(new_lines + [line]) if new_lines else line
                    if len(candidate) > max_len:
                        break
                    new_lines.append(line)
                if new_lines:
                    new_lines.append(f"\n<i>... (показано {displayed_count} из {total_sources} источников, страница {page}/{total_pages})</i>")
                text = "\n".join(new_lines)

        # Строим inline‑клавиатуру с навигацией по страницам + действия с БЗ
        nav_buttons: list[InlineKeyboardButton] = []
        if total_sources > 0:
            if page > 1:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ Назад", callback_data=f"kb_sources:{kb_id}:{page-1}")
                )
            if page * page_size < total_sources:
                nav_buttons.append(
                    InlineKeyboardButton("Вперёд ➡️", callback_data=f"kb_sources:{kb_id}:{page+1}")
                )

        kb_actions = kb_actions_menu(kb_id)
        kb_buttons = list(kb_actions.inline_keyboard)  # Преобразуем tuple в list
        if nav_buttons:
            keyboard = InlineKeyboardMarkup([nav_buttons] + kb_buttons)
        else:
            keyboard = kb_actions

        # Отправляем с HTML форматированием
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')
        except BadRequest as e:
            logger.warning("Ошибка форматирования HTML в списке источников: %s", e)
            import re

            text_plain = re.sub(r"<[^>]+>", "", text)
            await safe_edit_message_text(query, text_plain, reply_markup=keyboard)
        return
    
    if data.startswith('kb_clear:'):
        kb_id = int(data.split(':')[1])
        context.user_data['confirm_action'] = f'kb_clear:{kb_id}'
        await safe_edit_message_text(query, "Вы уверены, что хотите очистить базу знаний?", reply_markup=confirm_menu('kb_clear', str(kb_id)))
        return
    
    if data.startswith('kb_delete:'):
        kb_id = int(data.split(':')[1])
        context.user_data['confirm_action'] = f'kb_delete:{kb_id}'
        await safe_edit_message_text(query, "Вы уверены, что хотите удалить базу знаний?", reply_markup=confirm_menu('kb_delete', str(kb_id)))
        return
    
    if data.startswith('upload_type:'):
        doc_type = data.split(':')[1]
        kb_id = context.user_data.get('kb_id')
        
        if doc_type == 'web':
            context.user_data['state'] = 'waiting_url'
            await safe_edit_message_text(query, "Введите URL веб-страницы:")
        elif doc_type == 'image':
            context.user_data['kb_id'] = kb_id
            await safe_edit_message_text(query, "Отправьте изображение для обработки и добавления в базу знаний:")
        elif doc_type == 'zip':
            context.user_data['kb_id'] = kb_id
            await safe_edit_message_text(
                query,
                "📦 Отправьте ZIP архив с документами.\n\n"
                "Бот автоматически извлечет и обработает все поддерживаемые файлы из архива:\n"
                "• Markdown (.md)\n"
                "• Текстовые файлы (.txt)\n"
                "• Word документы (.docx)\n"
                "• Excel таблицы (.xlsx)\n"
                "• PDF файлы (.pdf)\n"
                "• Изображения (.jpg, .png и др.)\n\n"
                "После обработки вы получите отчет о загруженных файлах."
            )
        else:
            context.user_data['kb_id'] = kb_id
            await safe_edit_message_text(query, f"Отправьте файл типа {doc_type}")
        return
    
    # Подтверждение действий
    if data.startswith('confirm:'):
        parts = data.split(':')
        action = parts[1]
        item_id = parts[2] if len(parts) > 2 else None
        
        if action == 'kb_clear' and item_id:
            kb_id = int(item_id)
            ok = backend_client.clear_knowledge_base(kb_id)
            if ok:
                await safe_edit_message_text(query, "✅ База знаний очищена!", reply_markup=admin_menu())
            else:
                await safe_edit_message_text(query, "❌ Ошибка очистки базы знаний (backend)")
            return
        
        if action == 'kb_delete' and item_id:
            kb_id = int(item_id)
            ok = await asyncio.to_thread(backend_client.delete_knowledge_base, kb_id)
            if ok:
                await safe_edit_message_text(query, "✅ База знаний удалена!", reply_markup=admin_menu())
            else:
                await safe_edit_message_text(query, "❌ Ошибка удаления базы знаний (backend)")
            return
    
    if data == 'cancel':
        await safe_edit_message_text(query, "Действие отменено", reply_markup=admin_menu())
        return
    
    # Настройки ИИ
    if data == 'admin_ai':
        providers = ai_manager.list_providers()
        current = ai_manager.current_provider or 'ollama'
        text = f"🔧 Настройки ИИ\n\nТекущий провайдер: {current}\nДоступные провайдеры: {', '.join(providers)}"
        await safe_edit_message_text(query, text, reply_markup=ai_providers_menu(providers, current))
        return

    if data == 'admin_asr':
        settings = await asyncio.to_thread(backend_client.get_asr_settings)
        provider = settings.get("asr_provider", "transformers")
        model = settings.get("asr_model_name", "openai/whisper-large-v3-turbo")
        device = settings.get("asr_device") or "auto"
        show_meta = settings.get("show_asr_metadata", True)
        
        text = (
            "🎙️ Настройки распознавания речи\n\n"
            f"Провайдер: {provider}\n"
            f"Текущая модель: <code>{model}</code>\n"
            f"Устройство: {device}\n"
            f"Глобальный показ тех. инфо: {'ВКЛ' if show_meta else 'ВЫКЛ'}\n\n"
            "Выберите модель из списка или введите свое название."
        )
        
        asr_meta_label = "✅ Глобальное тех. инфо" if show_meta else "⚪ Глобальное тех. инфо"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Выбрать модель", callback_data='asr_select_model_list')],
            [InlineKeyboardButton(asr_meta_label, callback_data='toggle_global_asr_metadata')],
            [InlineKeyboardButton("🔙 Админ-меню", callback_data='admin_menu')],
        ])
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode='HTML')
        return

    if data == 'asr_select_model_list':
        settings = await asyncio.to_thread(backend_client.get_asr_settings)
        current_model = settings.get("asr_model_name", "")
        await safe_edit_message_text(
            query, 
            "🎙️ Выберите модель ASR из популярных или введите свою:", 
            reply_markup=asr_models_menu(current_model)
        )
        return

    if data.startswith('asr_set_model_id:'):
        model_id = data.split(':', 1)[1]
        await safe_edit_message_text(query, f"⏳ Проверяю и устанавливаю модель <code>{model_id}</code>...", parse_mode='HTML')
        
        try:
            # Отправляем запрос на обновление в бэкенд
            # Бэкенд теперь сам проверяет существование модели
            await asyncio.to_thread(
                backend_client.update_asr_settings,
                {"asr_model_name": model_id}
            )
            await query.answer(f"✅ Модель {model_id} установлена", show_alert=True)
            # Возвращаемся в основное меню ASR
            data = 'admin_asr'
            # Рекурсивно вызываем или просто повторяем логику
            settings = await asyncio.to_thread(backend_client.get_asr_settings)
            provider = settings.get("asr_provider", "transformers")
            model = settings.get("asr_model_name", "openai/whisper-large-v3-turbo")
            device = settings.get("asr_device") or "auto"
            show_meta = settings.get("show_asr_metadata", True)
            text = (
                "🎙️ Настройки распознавания речи\n\n"
                f"Провайдер: {provider}\n"
                f"Текущая модель: <code>{model}</code>\n"
                f"Устройство: {device}\n"
                f"Глобальный показ тех. инфо: {'ВКЛ' if show_meta else 'ВЫКЛ'}\n\n"
                "✅ Модель успешно обновлена!"
            )
            asr_meta_label = "✅ Глобальное тех. инфо" if show_meta else "⚪ Глобальное тех. инфо"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Выбрать модель", callback_data='asr_select_model_list')],
                [InlineKeyboardButton(asr_meta_label, callback_data='toggle_global_asr_metadata')],
                [InlineKeyboardButton("🔙 Админ-меню", callback_data='admin_menu')],
            ])
            await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Ошибка при смене модели ASR: {e}")
            error_text = str(e)
            if "400" in error_text:
                msg = f"❌ Ошибка: Модель <code>{model_id}</code> не найдена на Hugging Face.\n\nПроверьте название и попробуйте снова."
            else:
                msg = f"❌ Ошибка при смене модели: {error_text}"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к списку", callback_data='asr_select_model_list')],
            ])
            await safe_edit_message_text(query, msg, reply_markup=keyboard, parse_mode='HTML')
        return

    if data == 'asr_model_custom':
        context.user_data['state'] = 'waiting_asr_model'
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data='asr_select_model_list')],
        ])
        await safe_edit_message_text(
            query,
            "Введите полное название модели с Hugging Face (например, <code>openai/whisper-large-v3-turbo</code>):",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        return

    if data == 'admin_n8n':
        await safe_edit_message_text(query, _n8n_status_text(), reply_markup=n8n_menu(N8N_PUBLIC_URL or None))
        return

    if data == 'n8n_ping':
        ok, details = n8n_client.health_check()
        prefix = "✅ n8n доступен" if ok else "❌ Не удалось связаться с n8n"
        text = f"{prefix}\n{details}\n\n" \
               "Убедитесь, что сервис n8n запущен и переменные окружения настроены правильно."
        await safe_edit_message_text(query, text, reply_markup=n8n_menu(N8N_PUBLIC_URL or None))
        return

    if data == 'n8n_test_event':
        payload = {
            "telegram_id": user.get("telegram_id"),
            "username": user.get("username"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": "manual_test",
        }
        ok, details = n8n_client.send_event("bot_manual_test", payload)
        prefix = "✅ Тестовое событие отправлено" if ok else "❌ Не удалось отправить событие"
        text = f"{prefix}\n{details}"
        await safe_edit_message_text(query, text, reply_markup=n8n_menu(N8N_PUBLIC_URL or None))
        return
    
    # ── Chat Analytics ──────────────────────────────────────────────────
    if data == 'admin_analytics':
        from frontend.templates.buttons import analytics_menu
        await safe_edit_message_text(
            query,
            "📊 <b>Аналитика чатов</b>\n\n"
            "Управление сбором сообщений, импортом истории и генерацией дайджестов.",
            reply_markup=analytics_menu(),
            parse_mode='HTML',
        )
        return

    if data == 'analytics_select_chat':
        from frontend.templates.buttons import analytics_menu
        configs = await asyncio.to_thread(backend_client.analytics_list_configs)
        if not configs:
            await safe_edit_message_text(
                query,
                "Нет настроенных чатов.\n\n"
                "Добавьте бота в группу и включите сбор сообщений.",
                reply_markup=analytics_menu(),
            )
            return
        buttons = []
        for c in configs:
            cid = c.get('chat_id', '')
            title = c.get('chat_title') or cid
            buttons.append([InlineKeyboardButton(f"💬 {title}", callback_data=f"a_chat:{cid}")])
        buttons.append([InlineKeyboardButton("🔙 К аналитике", callback_data='admin_analytics')])
        await safe_edit_message_text(query, "Выберите чат:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith('a_chat:'):
        from frontend.templates.buttons import analytics_chat_menu
        chat_id_val = data.split(':', 1)[1]
        config = await asyncio.to_thread(backend_client.analytics_get_config, chat_id_val)
        if not config:
            config = {}
        title = config.get('chat_title') or chat_id_val
        collection = "ВКЛ" if config.get('collection_enabled', True) else "ВЫКЛ"
        cron = config.get('digest_cron') or 'не задано'
        period = config.get('digest_period_hours', 168)
        text = (
            f"📊 <b>{title}</b>\n\n"
            f"Сбор: {collection}\n"
            f"Расписание: {cron}\n"
            f"Период: {period}ч"
        )
        await safe_edit_message_text(query, text, reply_markup=analytics_chat_menu(chat_id_val, config), parse_mode='HTML')
        return

    if data.startswith('a_toggle:'):
        from frontend.templates.buttons import analytics_chat_menu
        chat_id_val = data.split(':', 1)[1]
        config = await asyncio.to_thread(backend_client.analytics_get_config, chat_id_val)
        current = config.get('collection_enabled', True) if config else True
        await asyncio.to_thread(backend_client.analytics_update_config, chat_id_val, {'collection_enabled': not current})
        new_status = "ВЫКЛ" if current else "ВКЛ"
        await query.answer(f"Сбор сообщений: {new_status}")
        updated = await asyncio.to_thread(backend_client.analytics_get_config, chat_id_val)
        await safe_edit_message_text(
            query, f"Сбор сообщений: {new_status}",
            reply_markup=analytics_chat_menu(chat_id_val, updated or {}),
        )
        return

    if data.startswith('a_schedule:'):
        from frontend.templates.buttons import analytics_schedule_menu
        chat_id_val = data.split(':', 1)[1]
        await safe_edit_message_text(query, "Выберите расписание:", reply_markup=analytics_schedule_menu(chat_id_val))
        return

    if data.startswith('a_cron:'):
        from frontend.templates.buttons import analytics_chat_menu
        parts = data.split(':')
        chat_id_val = parts[1]
        cron_expr = parts[2]
        period_hours = int(parts[3])
        await asyncio.to_thread(
            backend_client.analytics_update_config, chat_id_val,
            {'digest_cron': cron_expr, 'digest_period_hours': period_hours, 'analysis_enabled': True},
        )
        await query.answer("Расписание обновлено!")
        config = await asyncio.to_thread(backend_client.analytics_get_config, chat_id_val)
        await safe_edit_message_text(
            query, f"✅ Расписание: {cron_expr} (период: {period_hours}ч)",
            reply_markup=analytics_chat_menu(chat_id_val, config or {}),
        )
        return

    if data.startswith('a_cron_off:'):
        from frontend.templates.buttons import analytics_chat_menu
        chat_id_val = data.split(':', 1)[1]
        await asyncio.to_thread(
            backend_client.analytics_update_config, chat_id_val,
            {'digest_cron': None, 'analysis_enabled': False},
        )
        await query.answer("Расписание отключено")
        config = await asyncio.to_thread(backend_client.analytics_get_config, chat_id_val)
        await safe_edit_message_text(query, "Расписание отключено.", reply_markup=analytics_chat_menu(chat_id_val, config or {}))
        return

    if data.startswith('a_gen_now:'):
        from frontend.templates.buttons import analytics_generate_period_menu
        chat_id_val = data.split(':', 1)[1]
        await safe_edit_message_text(query, "Период для дайджеста:", reply_markup=analytics_generate_period_menu(chat_id_val))
        return

    if data.startswith('a_gen:'):
        from frontend.templates.buttons import analytics_menu
        parts = data.split(':')
        chat_id_val = parts[1]
        period_hours = int(parts[2])
        from datetime import timedelta, timezone as tz
        period_end = datetime.now(tz.utc)
        period_start = period_end - timedelta(hours=period_hours)
        request = {
            'chat_id': chat_id_val,
            'period_start': period_start.isoformat(),
            'period_end': period_end.isoformat(),
        }
        result = await asyncio.to_thread(backend_client.analytics_generate_digest, request)
        if result and result.get('digest_id'):
            digest_id = result['digest_id']
            await safe_edit_message_text(
                query,
                f"🚀 Генерация запущена!\nDigest ID: {digest_id}\nПериод: {period_hours}ч",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data=f"a_dstatus:{digest_id}")],
                    [InlineKeyboardButton("🔙 К аналитике", callback_data='admin_analytics')],
                ]),
            )
        else:
            await safe_edit_message_text(query, "❌ Не удалось запустить.", reply_markup=analytics_menu())
        return

    if data.startswith('a_dstatus:'):
        from frontend.templates.buttons import analytics_menu
        digest_id = int(data.split(':', 1)[1])
        result = await asyncio.to_thread(backend_client.analytics_get_digest, digest_id)
        if not result:
            await safe_edit_message_text(query, "Дайджест не найден.", reply_markup=analytics_menu())
            return
        status = result.get('status', 'unknown')
        text = f"📋 Дайджест #{digest_id}\n\nСтатус: {status}"
        if status == 'completed':
            summary = result.get('summary_text', '')
            theme_count = result.get('theme_count', 0)
            total = result.get('total_messages_analyzed', 0)
            gen_time = result.get('generation_time_sec', 0)
            text = (
                f"✅ Дайджест #{digest_id}\n\n"
                f"Тем: {theme_count}, сообщений: {total}, время: {gen_time}с\n\n"
                f"{summary[:3000] if summary else '(пусто)'}"
            )
        elif status == 'failed':
            error = result.get('error_message', '')
            text = f"❌ Дайджест #{digest_id} — ошибка\n\n{error}"
        buttons = []
        if status in ('pending', 'generating'):
            buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"a_dstatus:{digest_id}")])
        buttons.append([InlineKeyboardButton("🔙 К аналитике", callback_data='admin_analytics')])
        try:
            await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=None)
        except Exception:
            await safe_edit_message_text(query, text[:4000], reply_markup=InlineKeyboardMarkup(buttons), parse_mode=None)
        return

    if data == 'analytics_digests':
        from frontend.templates.buttons import analytics_menu
        configs = await asyncio.to_thread(backend_client.analytics_list_configs)
        if not configs:
            await safe_edit_message_text(query, "Нет настроенных чатов.", reply_markup=analytics_menu())
            return
        buttons = []
        for c in configs:
            cid = c.get('chat_id', '')
            title = c.get('chat_title') or cid
            buttons.append([InlineKeyboardButton(f"📋 {title}", callback_data=f"a_dlist:{cid}")])
        buttons.append([InlineKeyboardButton("🔙 К аналитике", callback_data='admin_analytics')])
        await safe_edit_message_text(query, "Выберите чат:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith('a_dlist:'):
        from frontend.templates.buttons import analytics_menu
        chat_id_val = data.split(':', 1)[1]
        import httpx
        url = backend_client._url(f"/analytics/digests?chat_id={chat_id_val}&limit=10")
        headers = {"X-API-Key": backend_client.api_key} if backend_client.api_key else {}
        try:
            with httpx.Client(timeout=backend_client.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                digests = resp.json()
        except Exception:
            digests = []
        if not digests:
            await safe_edit_message_text(query, f"Нет дайджестов для {chat_id_val}.", reply_markup=analytics_menu())
            return
        buttons = []
        for d in digests[:10]:
            did = d.get('id', 0)
            st = d.get('status', '?')
            tc = d.get('theme_count', 0)
            buttons.append([InlineKeyboardButton(f"#{did} [{st}] {tc} тем", callback_data=f"a_dstatus:{did}")])
        buttons.append([InlineKeyboardButton("🔙 К аналитике", callback_data='admin_analytics')])
        await safe_edit_message_text(query, f"Дайджесты для {chat_id_val}:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == 'analytics_import':
        from frontend.templates.buttons import analytics_menu, analytics_import_chat_menu
        configs = await asyncio.to_thread(backend_client.analytics_list_configs)
        chat_list = [{'chat_id': c.get('chat_id', ''), 'chat_title': c.get('chat_title')} for c in (configs or [])]
        if not chat_list:
            await safe_edit_message_text(
                query, "Нет настроенных чатов для импорта.\nДобавьте бота в группу.",
                reply_markup=analytics_menu(),
            )
            return
        await safe_edit_message_text(query, "📥 Выберите чат для импорта:", reply_markup=analytics_import_chat_menu(chat_list))
        return

    if data.startswith('a_import_to:'):
        chat_id_val = data.split(':', 1)[1]
        context.user_data['analytics_import_chat_id'] = chat_id_val
        context.user_data['state'] = 'waiting_analytics_import'
        await safe_edit_message_text(
            query,
            f"📥 Импорт в чат {chat_id_val}\n\n"
            "Отправьте файл экспорта (JSON, HTML, CSV, TXT).\n\n"
            "Telegram Desktop: Settings → Advanced → Export chat history → JSON.",
        )
        return

    if data == 'analytics_stats':
        from frontend.templates.buttons import analytics_menu
        configs = await asyncio.to_thread(backend_client.analytics_list_configs)
        if not configs:
            await safe_edit_message_text(query, "Нет настроенных чатов.", reply_markup=analytics_menu())
            return
        buttons = []
        for c in configs:
            cid = c.get('chat_id', '')
            title = c.get('chat_title') or cid
            buttons.append([InlineKeyboardButton(f"📈 {title}", callback_data=f"a_stats:{cid}")])
        buttons.append([InlineKeyboardButton("🔙 К аналитике", callback_data='admin_analytics')])
        await safe_edit_message_text(query, "Выберите чат:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith('a_stats:'):
        from frontend.templates.buttons import analytics_menu
        chat_id_val = data.split(':', 1)[1]
        stats = await asyncio.to_thread(backend_client.analytics_get_stats, chat_id_val)
        if not stats:
            await safe_edit_message_text(query, f"Нет данных для {chat_id_val}.", reply_markup=analytics_menu())
            return
        total = stats.get('total_messages', 0)
        authors = stats.get('unique_authors', 0)
        threads = stats.get('active_threads', 0)
        per_day = stats.get('messages_per_day', 0)
        top_authors = stats.get('top_authors', [])
        text = (
            f"📈 <b>Статистика: {chat_id_val}</b>\n\n"
            f"Всего сообщений: {total}\n"
            f"Авторов: {authors}\n"
            f"Тем: {threads}\n"
            f"Сообщений/день: {per_day}\n"
        )
        if top_authors:
            text += "\n<b>Топ авторов:</b>\n"
            for a in top_authors[:5]:
                text += f"  • {a.get('name', '?')} — {a.get('count', 0)}\n"
        await safe_edit_message_text(query, text, reply_markup=analytics_menu(), parse_mode='HTML')
        return

    # Загрузка документов (общее меню)
    if data == 'admin_upload':
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        if not kbs:
            await safe_edit_message_text(query, "Сначала создайте базу знаний!", reply_markup=admin_menu())
        else:
            await safe_edit_message_text(query, "Выберите базу знаний для загрузки:", reply_markup=knowledge_base_menu(kbs))
        return

