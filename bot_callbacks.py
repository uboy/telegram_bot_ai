"""
Обработчики callback'ов для кнопок
"""
import os
import tempfile
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from database import Session, User, KnowledgeBase, KnowledgeChunk, KnowledgeImportLog
from ai_providers import ai_manager
from rag_system import rag_system
from document_loaders import document_loader_manager
from image_processor import image_processor
from templates.buttons import (
    main_menu, admin_menu, settings_menu, ai_providers_menu, ollama_models_menu,
    user_management_menu, knowledge_base_menu, kb_actions_menu,
    document_type_menu, confirm_menu, n8n_menu, rag_settings_menu
)
try:
    from config import ADMIN_IDS, N8N_PUBLIC_URL
except ImportError:
    # Fallback если config.py не найден
    import os
    ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_STR.split(",") if id.strip()] if ADMIN_IDS_STR else []
    N8N_PUBLIC_URL = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678")
from logging_config import logger
from n8n_client import n8n_client

session = Session()


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
        # Читаем весь файл
        with open(env_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Ищем переменную и обновляем её значение
        found = False
        updated_lines = []
        
        for line in lines:
            stripped = line.strip()
            # Проверяем, является ли строка нашей переменной (с учетом комментариев)
            if stripped.startswith(f"{var_name}=") and not stripped.startswith('#'):
                # Обновляем значение
                updated_lines.append(f"{var_name}={var_value}\n")
                found = True
            elif stripped.startswith(f"# {var_name}="):
                # Если переменная закомментирована, раскомментируем и обновим
                updated_lines.append(f"{var_name}={var_value}\n")
                found = True
            else:
                updated_lines.append(line)
        
        # Если переменная не найдена, добавляем в конец
        if not found:
            updated_lines.append(f"\n# RAG Configuration\n")
            updated_lines.append(f"{var_name}={var_value}\n")
        
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
        if 'button_data_invalid' in error_msg or 'inline keyboard expected' in error_msg or 'message is not modified' in error_msg:
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
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    if not user or not user.approved:
        await safe_edit_message_text(query, "Вы не одобрены для использования бота.")
        return
    
    # Обработка одобрения/отклонения пользователей (только для админов)
    if data.startswith("approve:") or data.startswith("decline:"):
        if user_id not in [str(aid) for aid in ADMIN_IDS]:
            return
        
        _, tg_id = data.split(":")
        target_user = session.query(User).filter_by(telegram_id=tg_id).first()
        if target_user:
            if data.startswith("approve:"):
                target_user.approved = True
                target_user.role = 'user'
                session.commit()
                await safe_edit_message_text(query, "✅ Пользователь одобрен")
                try:
                    await context.bot.send_message(
                        chat_id=int(tg_id),
                        text="✅ Ваша заявка одобрена! Теперь вы можете использовать бота.",
                        reply_markup=main_menu()
                    )
                except:
                    pass
            else:
                session.delete(target_user)
                session.commit()
                await safe_edit_message_text(query, "❌ Пользователь отклонен")
        return
    
    # Главное меню
    if data == 'main_menu':
        menu = main_menu(is_admin=(user.role == 'admin'))
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
        await safe_edit_message_text(query, "⚙️ Настройки:", reply_markup=settings_menu())
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
            user.preferred_provider = provider_name
            session.commit()
            
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
            
            current_model = user.preferred_model or (provider.model if hasattr(provider, 'model') else '')
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
            
            current_model = getattr(user, 'preferred_image_model', '') or (provider.model if hasattr(provider, 'model') else '')
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
        # Формат: ollama_model:<target>:<model_name> или ollama_model:<target>:hash:<hash>
        parts = data.split(':', 3)
        if len(parts) < 3:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return
        
        target = parts[1]
        model_identifier = parts[2] if len(parts) > 2 else ''
        
        # Если используется хеш, получаем модель из сохраненного списка
        if model_identifier == 'hash' and len(parts) > 3:
            model_hash = parts[3]
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
            # Прямое имя модели (для коротких имен)
            model_name = model_identifier

        if not model_name:
            await query.answer("Некорректное имя модели", show_alert=True)
            return

        if target == 'image':
            user.preferred_image_model = model_name
            message = f"✅ Модель для изображений изменена на {model_name}"
        else:
            user.preferred_model = model_name
            message = f"✅ Модель для текста изменена на {model_name}"

        session.commit()
        await safe_edit_message_text(query, message, reply_markup=settings_menu())
        return
    
    # Настройки RAG
    if data == 'rag_settings':
        from config import RAG_MODEL_NAME, RAG_RERANK_MODEL
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
            from config import RAG_MODEL_NAME
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
            from config import RAG_RERANK_MODEL
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
        
        # Формат: rag_embedding_model:model_name или rag_embedding_model:hash:XXXXXXXX
        parts = data.split(':', 2)
        model_type = parts[0]
        
        if len(parts) < 2:
            await query.answer("Некорректный формат callback_data", show_alert=True)
            return
        
        # Проверяем, используется ли хеш
        if len(parts) == 3 and parts[1] == 'hash':
            model_hash = parts[2]
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
            # Прямое имя модели (для коротких имен)
            model_name = parts[1] if len(parts) > 1 else ''
        
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
        # Перезагрузить модели RAG в рантайме
        try:
            await safe_edit_message_text(query, "🔄 Перезагрузка моделей RAG...\n\nЭто может занять некоторое время.")
            
            result = rag_system.reload_models()
            
            if result['embedding'] and result['reranker']:
                message = (
                    "✅ Модели RAG успешно перезагружены!\n\n"
                    "• Модель эмбеддингов: перезагружена\n"
                    "• Модель ранкинга: перезагружена\n\n"
                    "Изменения применены без перезапуска бота."
                )
            elif result['embedding']:
                message = (
                    "⚠️ Частичная перезагрузка моделей RAG:\n\n"
                    "• Модель эмбеддингов: ✅ перезагружена\n"
                    "• Модель ранкинга: ❌ ошибка перезагрузки\n\n"
                    "Проверьте логи для деталей."
                )
            elif result['reranker']:
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
            logger.error(f"Ошибка при перезагрузке моделей RAG: {e}", exc_info=True)
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
    if user.role == 'admin':
        await handle_admin_callbacks(query, context, data, user)
    else:
        await query.answer("У вас нет прав администратора", show_alert=True)


def _build_users_page_keyboard(users, page: int, page_size: int = 5) -> InlineKeyboardMarkup:
    """Сформировать inline-клавиатуру для списка пользователей с пагинацией.

    Для каждого пользователя:
      1) Кнопка \"одобрить/сменить роль\"
      2) Кнопка \"удалить\"
    """
    total = len(users)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    end = start + page_size
    page_users = users[start:end]

    buttons: list[list[InlineKeyboardButton]] = []

    for u in page_users:
        # Определяем подпись для кнопки смены роли / акцепта
        if not u.approved:
            toggle_label = "✅ Одобрить / user"
        else:
            if (u.role or "user") == "admin":
                toggle_label = "🔁 admin → user"
            else:
                toggle_label = "🔁 user → admin"

        buttons.append(
            [
                InlineKeyboardButton(
                    toggle_label,
                    callback_data=f"user_toggle:{u.id}:{page}",
                ),
                InlineKeyboardButton(
                    "🗑️ Удалить",
                    callback_data=f"user_delete:{u.id}:{page}",
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


async def handle_admin_callbacks(query, context, data: str, user: User):
    """Обработка админских callback'ов"""
    
    # Админ-меню
    if data == 'admin_menu':
        await safe_edit_message_text(query, "👨‍💼 Админ-панель:", reply_markup=admin_menu())
        return
    
    # Управление пользователями
    if data == 'admin_users':
        # Показать первую страницу списка пользователей
        users = session.query(User).order_by(User.id.asc()).all()
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
            full_name = getattr(u, "full_name", None) or "-"
            username = f"@{u.username}" if u.username else "-"
            phone = getattr(u, "phone", None) or "не указан"
            status = "✅ одобрен" if u.approved else "⏳ заявка"
            role = u.role or "user"

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(u.telegram_id)}</code>\n"
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
        users = session.query(User).order_by(User.id.asc()).all()
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
            full_name = getattr(u, "full_name", None) or "-"
            username = f"@{u.username}" if u.username else "-"
            phone = getattr(u, "phone", None) or "не указан"
            status = "✅ одобрен" if u.approved else "⏳ заявка"
            role = u.role or "user"

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(u.telegram_id)}</code>\n"
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

        target_user = session.query(User).get(target_id)
        if not target_user:
            await query.answer("Пользователь не найден", show_alert=True)
            return

        # Если пользователь ещё не одобрен — одобряем и ставим роль user
        if not target_user.approved:
            target_user.approved = True
            target_user.role = "user"
        else:
            # Меняем роль на противоположную между user/admin
            target_user.role = "admin" if (target_user.role or "user") == "user" else "user"

        session.commit()

        # Перерисуем текущую страницу
        users = session.query(User).order_by(User.id.asc()).all()
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
            full_name = getattr(u, "full_name", None) or "-"
            username = f"@{u.username}" if u.username else "-"
            phone = getattr(u, "phone", None) or "не указан"
            status = "✅ одобрен" if u.approved else "⏳ заявка"
            role = u.role or "user"

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(u.telegram_id)}</code>\n"
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

        target_user = session.query(User).get(target_id)
        if not target_user:
            await query.answer("Пользователь не найден", show_alert=True)
            return

        session.delete(target_user)
        session.commit()

        users = session.query(User).order_by(User.id.asc()).all()
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
            full_name = getattr(u, "full_name", None) or "-"
            username = f"@{u.username}" if u.username else "-"
            phone = getattr(u, "phone", None) or "не указан"
            status = "✅ одобрен" if u.approved else "⏳ заявка"
            role = u.role or "user"

            lines.append(
                f"{idx}. <b>{escape(full_name)}</b>\n"
                f"   Логин: {escape(username)}\n"
                f"   ID: <code>{escape(u.telegram_id)}</code>\n"
                f"   Телефон: {escape(phone)}\n"
                f"   Роль: {escape(role)}, Статус: {status}\n"
            )

        text = "\n".join(lines)
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="HTML")
        return
    
    # Управление базами знаний
    if data == 'admin_kb':
        kbs = rag_system.list_knowledge_bases()
        await safe_edit_message_text(query, "📚 Базы знаний:", reply_markup=knowledge_base_menu(kbs))
        return
    
    if data == 'kb_create':
        context.user_data['state'] = 'waiting_kb_name'
        await safe_edit_message_text(query, "Введите название новой базы знаний:")
        return
    
    if data.startswith('kb_select:'):
        kb_id = int(data.split(':')[1])
        kb = rag_system.get_knowledge_base(kb_id)
        if kb:
            chunks_count = session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).count()
            text = f"📚 База знаний: {kb.name}\n\nОписание: {kb.description or 'Нет описания'}\nФрагментов: {chunks_count}"
            
            # Проверить, есть ли ожидающий документ для загрузки
            if 'pending_document' in context.user_data:
                # Установить базу знаний и загрузить документ
                context.user_data['kb_id'] = kb_id
                pending = context.user_data.pop('pending_document')
                
                # Загрузить документ асинхронно
                from bot_handlers import load_document_to_kb
                await safe_edit_message_text(query, "📤 Загружаю документ...")
                await load_document_to_kb(query, context, pending, kb_id)
                return
            
            await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
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
            from wiki_git_loader import load_wiki_from_git_async
            
            stats = await load_wiki_from_git_async(wiki_url, kb_id)
            deleted = stats.get("deleted_chunks", 0)
            files = stats.get("files_processed", 0)
            added = stats.get("chunks_added", 0)
            wiki_root = stats.get("wiki_root", wiki_url)
            
            # Обновить индекс RAG системы для доступа к новым чанкам
            try:
                rag_system.index = None
                rag_system.chunks = []
                logger.info("[wiki-git] Индекс RAG системы сброшен, будет пересоздан при следующем поиске")
            except Exception as idx_error:
                logger.warning(f"[wiki-git] Не удалось обновить индекс: {idx_error}")
            
            # Записать в журнал загрузок
            tg_id = str(query.from_user.id) if query.from_user else ""
            db_user = session.query(User).filter_by(telegram_id=tg_id).first() if tg_id else None
            username = db_user.username if db_user else tg_id
            user_info = {"telegram_id": tg_id, "username": username}
            log = KnowledgeImportLog(
                knowledge_base_id=kb_id,
                user_telegram_id=tg_id,
                username=username,
                action_type="wiki_git",
                source_path=wiki_root,
                total_chunks=added,
            )
            session.add(log)
            session.commit()
            
            try:
                from bot_handlers import emit_n8n_import_event
                emit_n8n_import_event(
                kb_id=kb_id,
                action_type="wiki_git",
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
                "✅ Загрузка вики через git завершена.\n\n"
                f"Исходный URL: {wiki_url}\n"
                f"Корневой wiki-URL: {wiki_root}\n"
                f"Удалено старых фрагментов: {deleted}\n"
                f"Обработано файлов: {files}\n"
                f"Добавлено фрагментов: {added}"
            )
            await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
        except Exception as e:
            logger.error(f"Ошибка при загрузке вики через git: {e}", exc_info=True)
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
        logs = (
            session.query(KnowledgeImportLog)
            .filter_by(knowledge_base_id=kb_id)
            .order_by(KnowledgeImportLog.created_at.desc())
            .limit(20)
            .all()
        )
        if not logs:
            text = "Журнал загрузок пуст для этой базы знаний."
        else:
            lines = ["📜 Журнал последних загрузок:\n"]
            for log in logs:
                when = log.created_at.strftime("%Y-%m-%d %H:%M")
                who = log.username or log.user_telegram_id or "?"
                lines.append(
                    f"- {when} — {who} — {log.action_type} — {log.source_path} "
                    f"(фрагментов: {log.total_chunks})"
                )
            text = "\n".join(lines)
        await safe_edit_message_text(query, text, reply_markup=kb_actions_menu(kb_id))
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

        # Получить уникальные источники из chunks с датой последнего обновления
        from sqlalchemy import func, distinct
        from urllib.parse import urlparse, parse_qs
        
        def _normalize_wiki_url_for_display(url: str) -> str:
            """Нормализовать URL вики для отображения (конвертировать export URL в читаемый формат)"""
            if not url or not url.startswith(('http://', 'https://')):
                return url
            
            # Если это export URL Gitee вики, пытаемся найти оригинальный URL в метаданных
            # или конвертируем в базовый формат вики
            if '/wikis/pages/export' in url:
                try:
                    parsed = urlparse(url)
                    query_params = parse_qs(parsed.query)
                    
                    # Пытаемся найти оригинальный URL из метаданных chunks
                    # Для этого ищем chunk с таким source_path и проверяем metadata
                    chunk = session.query(KnowledgeChunk).filter_by(
                        knowledge_base_id=kb_id,
                        source_path=url
                    ).first()
                    
                    if chunk and chunk.chunk_metadata:
                        import json
                        try:
                            meta = json.loads(chunk.chunk_metadata)
                            # Если в метаданных есть оригинальный URL или title, используем его
                            if 'original_url' in meta:
                                return meta['original_url']
                            if 'wiki_page_url' in meta:
                                return meta['wiki_page_url']
                        except:
                            pass
                    
                    # Если не нашли, конвертируем в базовый формат
                    # Для Gitee: /wikis/pages/export?doc_id=... -> /wikis/...
                    # Но без doc_id мы не можем точно восстановить путь страницы
                    # Поэтому просто возвращаем базовый путь вики
                    path_parts = parsed.path.split('/wikis')
                    if len(path_parts) >= 2:
                        base_path = path_parts[0] + '/wikis'
                        return f"{parsed.scheme}://{parsed.netloc}{base_path}"
                except Exception:
                    pass
            
            return url
        
        # Нормализовать URL перед группировкой для устранения дублирования
        # (убираем trailing slash, нормализуем параметры и т.д.)
        from urllib.parse import urlparse, urlunparse
        
        def normalize_source_path(path: str) -> str:
            """Нормализовать путь источника для группировки"""
            if not path:
                return path
            # Если это URL, нормализуем его
            if path.startswith(('http://', 'https://')):
                try:
                    parsed = urlparse(path)
                    # Для вики Gitee: каждая страница должна быть отдельным источником
                    # Не группируем страницы вики вместе
                    if '/wikis' in path:
                        # Для вики используем полный URL как ключ (не группируем)
                        return path
                    # Убираем trailing slash из path
                    normalized_path = parsed.path.rstrip('/') or '/'
                    # Для export URL оставляем как есть (не группируем)
                    if '/wikis/pages/export' in path:
                        return path
                    # Убираем query параметры для группировки (они могут различаться)
                    normalized = urlunparse((
                        parsed.scheme,
                        parsed.netloc,
                        normalized_path,
                        parsed.params,
                        '',  # query - убираем
                        ''   # fragment - убираем
                    ))
                    return normalized
                except Exception:
                    return path.rstrip('/')
            # Для не-URL (файлы) не группируем - каждый файл отдельный источник
            # Просто возвращаем как есть
            return path
        
        # Получаем источники с нормализованным путем
        # ВАЖНО: группируем по source_path, чтобы каждая страница вики была отдельным источником
        sources_query = (
            session.query(
                KnowledgeChunk.source_path,
                KnowledgeChunk.source_type,
                func.max(KnowledgeChunk.created_at).label('last_updated'),
                func.count(KnowledgeChunk.id).label('chunks_count')
            )
            .filter_by(knowledge_base_id=kb_id)
            .group_by(KnowledgeChunk.source_path, KnowledgeChunk.source_type)
            .all()
        )
        
        logger.debug(f"[kb_sources] Найдено уникальных источников в запросе: {len(sources_query)}")
        
        # Группируем по нормализованному пути
        # НО: для файлов (не URL) и вики не группируем - каждый файл/страница отдельный источник
        sources_dict = {}
        for source_path, source_type, last_updated, chunks_count in sources_query:
            if not source_path:
                continue
            
            # Для файлов (не URL) используем полный путь как ключ (не группируем)
            if not source_path.startswith(('http://', 'https://')):
                key = (source_path, source_type)
            elif '/wikis' in source_path:
                # Для вики используем полный URL как ключ (не группируем страницы)
                # ВАЖНО: используем исходный source_path как есть, без нормализации
                key = (source_path, source_type)
            else:
                # Для других URL нормализуем для группировки
                normalized_path = normalize_source_path(source_path)
                key = (normalized_path, source_type)
            
            if key not in sources_dict:
                sources_dict[key] = {
                    'source_path': source_path,  # Оригинальный путь для отображения
                    'source_type': source_type,
                    'last_updated': last_updated,
                    'chunks_count': chunks_count
                }
                logger.debug(f"[kb_sources] Добавлен источник: {source_path} ({chunks_count} чанков)")
            else:
                # Объединяем данные: берем максимальную дату и суммируем chunks
                # Это не должно происходить для вики, так как мы используем полный URL как ключ
                existing = sources_dict[key]
                if last_updated and (not existing['last_updated'] or last_updated > existing['last_updated']):
                    existing['last_updated'] = last_updated
                existing['chunks_count'] += chunks_count
                logger.warning(f"[kb_sources] Объединены источники с одинаковым ключом: {key} (это не должно происходить для вики)")
        
        # Преобразуем обратно в список и сортируем
        sources_list = list(sources_dict.values())
        total_sources = len(sources_list)
        logger.info(f"[kb_sources] Всего уникальных источников после группировки: {total_sources}")
        from datetime import datetime as dt_min
        sources_list.sort(key=lambda x: x['last_updated'] or dt_min.min.replace(tzinfo=timezone.utc), reverse=True)

        # Пагинация по источникам
        if total_sources == 0:
            text = "В этой базе знаний нет загруженных источников."
        else:
            total_pages = max(1, (total_sources + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_sources = sources_list[start_idx:end_idx]

            lines = [f"📋 <b>Список источников в базе знаний</b> (стр. {page}/{total_pages}):\n"]
            displayed_count = 0
            for source_data in page_sources:
                source_path = source_data['source_path']
                source_type = source_data['source_type']
                last_updated = source_data['last_updated']
                chunks_count = source_data['chunks_count']
                if '.keep' in (source_path or '').lower():
                    logger.debug(f"[kb_sources] Пропущен источник с .keep: {source_path}")
                    continue
                
                displayed_count += 1
                
                # Формируем отображаемое имя и ссылку
                from html import escape
                from urllib.parse import unquote
                
                # Проверяем, является ли источник URL (web тип или начинается с http/https)
                is_url = (source_type == 'web' or (source_path and source_path.startswith(('http://', 'https://'))))
                
                if is_url and source_path:
                    # Для URL создаем HTML ссылку
                    url_for_link = source_path
                    
                    # Нормализуем URL для вики (для отображения)
                    display_path = _normalize_wiki_url_for_display(source_path)
                    
                    # Извлекаем название из пути для отображения
                    if '/' in url_for_link:
                        # Берем последнюю непустую часть пути
                        parts = [p for p in url_for_link.split('/') if p]
                        if parts:
                            title = parts[-1]
                        else:
                            title = url_for_link
                    else:
                        title = url_for_link
                    
                    # Декодируем URL для читаемости (убираем %26 -> &, %20 -> пробел и т.д.)
                    title = unquote(title)
                    
                    # Если title все еще пустой или слишком короткий, используем предпоследнюю часть
                    if not title or len(title) < 2:
                        parts = [p for p in url_for_link.split('/') if p]
                        if len(parts) > 1:
                            title = unquote(parts[-2])
                        else:
                            title = url_for_link
                    
                    # Экранируем для HTML (только один раз!)
                    # Важно: escape только текст, не URL (Telegram сам обработает URL в href)
                    title_escaped = escape(title)
                    # URL экранируем только для атрибута href (но не двойное экранирование)
                    # Telegram требует, чтобы URL был правильно экранирован для HTML
                    url_escaped = escape(url_for_link)
                    path_display = f'<a href="{url_escaped}">{title_escaped}</a>'
                elif '::' in (source_path or ''):
                    # Для архивов показываем имя файла внутри архива
                    file_name = source_path.split('::')[-1]
                    # Декодируем если нужно
                    file_name = unquote(file_name) if '%' in file_name else file_name
                    path_display = f"<code>{escape(file_name)}</code>"
                elif '/' in (source_path or ''):
                    # Для обычных файлов показываем имя файла
                    file_name = source_path.split('/')[-1]
                    # Декодируем если нужно
                    file_name = unquote(file_name) if '%' in file_name else file_name
                    path_display = f"<code>{escape(file_name)}</code>"
                else:
                    # Для простых путей декодируем если нужно
                    path_to_display = unquote(source_path) if source_path and '%' in source_path else (source_path or 'не указан')
                    path_display = escape(path_to_display)
                
                date_str = last_updated.strftime("%Y-%m-%d %H:%M") if last_updated else "?"
                lines.append(f"• {path_display}")
                lines.append(f"  Тип: {source_type}, фрагментов: {chunks_count}, обновлено: {date_str}\n")
            
            # Собираем текст и при необходимости обрезаем ТОЛЬКО по целым строкам,
            # чтобы не рвать HTML-теги и не получать «unclosed start tag».
            full_text = "\n".join(lines)
            logger.info(f"[kb_sources] Отображается {displayed_count} источников из {total_sources} (страница {page})")
            
            max_len = 3900  # небольшой запас до лимита Telegram 4096
            if len(full_text) <= max_len:
                text = full_text
            else:
                new_lines = []
                for line in lines:
                    candidate = "\n".join(new_lines + [line]) if new_lines else line
                    if len(candidate) > max_len:
                        break
                    new_lines.append(line)
                text = "\n".join(new_lines)

        # Строим inline‑клавиатуру с навигацией по страницам + действия с БЗ
        nav_buttons = []
        if total_sources > 0:
            if page > 1:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ Назад", callback_data=f"kb_sources:{kb_id}:{page-1}")
                )
            if page * page_size < total_sources:
                nav_buttons.append(
                    InlineKeyboardButton("Вперёд ➡️", callback_data=f"kb_sources:{kb_id}:{page+1}")
                )

        kb_buttons = kb_actions_menu(kb_id).inline_keyboard  # type: ignore[attr-defined]
        if nav_buttons:
            keyboard = InlineKeyboardMarkup([nav_buttons] + kb_buttons)
        else:
            keyboard = kb_actions_menu(kb_id)

        # Отправляем с HTML форматированием
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')
        except BadRequest as e:
            # Если HTML не работает, отправляем без форматирования
            logger.warning(f"Ошибка форматирования HTML в списке источников: {e}")
            # Убираем HTML теги для простого текста
            import re
            text_plain = re.sub(r'<[^>]+>', '', text)
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
            if rag_system.clear_knowledge_base(kb_id):
                await safe_edit_message_text(query, "✅ База знаний очищена!", reply_markup=admin_menu())
            else:
                await safe_edit_message_text(query, "❌ Ошибка очистки базы знаний")
            return
        
        if action == 'kb_delete' and item_id:
            kb_id = int(item_id)
            if rag_system.delete_knowledge_base(kb_id):
                await safe_edit_message_text(query, "✅ База знаний удалена!", reply_markup=admin_menu())
            else:
                await safe_edit_message_text(query, "❌ Ошибка удаления базы знаний")
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
            "telegram_id": user.telegram_id,
            "username": user.username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": "manual_test",
        }
        ok, details = n8n_client.send_event("bot_manual_test", payload)
        prefix = "✅ Тестовое событие отправлено" if ok else "❌ Не удалось отправить событие"
        text = f"{prefix}\n{details}"
        await safe_edit_message_text(query, text, reply_markup=n8n_menu(N8N_PUBLIC_URL or None))
        return
    
    # Загрузка документов (общее меню)
    if data == 'admin_upload':
        kbs = rag_system.list_knowledge_bases()
        if not kbs:
            await safe_edit_message_text(query, "Сначала создайте базу знаний!", reply_markup=admin_menu())
        else:
            await safe_edit_message_text(query, "Выберите базу знаний для загрузки:", reply_markup=knowledge_base_menu(kbs))
        return

