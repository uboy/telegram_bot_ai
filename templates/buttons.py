"""
Расширенная система кнопок для бота
"""
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu(is_admin: bool = False):
    """Главное меню для пользователей"""
    # Основное меню теперь в виде обычной клавиатуры, чтобы кнопки были "на месте" у пользователя
    keyboard = [
        [KeyboardButton("🔍 Поиск в базе знаний")],
        [KeyboardButton("🌐 Поиск в интернете")],
        [KeyboardButton("🤖 Задать вопрос ИИ")],
        [KeyboardButton("🖼️ Обработать изображение")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton("👨‍💼 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def admin_menu():
    """Меню для администраторов"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Управление пользователями", callback_data='admin_users')],
        [InlineKeyboardButton("📚 Управление базами знаний", callback_data='admin_kb')],
        [InlineKeyboardButton("🔧 Настройки ИИ", callback_data='admin_ai')],
        [InlineKeyboardButton("🔗 Интеграция n8n", callback_data='admin_n8n')],
        [InlineKeyboardButton("📤 Загрузить документы", callback_data='admin_upload')],
        [InlineKeyboardButton("🔙 Главное меню", callback_data='main_menu')],
    ])


def settings_menu():
    """Меню настроек"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Выбрать ИИ провайдер", callback_data='select_provider')],
        [InlineKeyboardButton("💬 Модель для текста (Ollama)", callback_data='select_text_model')],
        [InlineKeyboardButton("🖼️ Модель для изображений (Ollama)", callback_data='select_image_model')],
        [InlineKeyboardButton("🔧 Настройки RAG", callback_data='rag_settings')],
        [InlineKeyboardButton("🔙 Главное меню", callback_data='main_menu')],
    ])

def rag_settings_menu():
    """Меню настроек RAG"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Модель эмбеддингов", callback_data='select_embedding_model')],
        [InlineKeyboardButton("🎯 Модель ранкинга", callback_data='select_rerank_model')],
        [InlineKeyboardButton("🔄 Перезагрузить модели", callback_data='rag_reload_models')],
        [InlineKeyboardButton("🔙 К настройкам", callback_data='settings')],
    ])


def ai_providers_menu(providers: list, current_provider: str):
    """Меню выбора провайдера ИИ"""
    buttons = []
    for provider in providers:
        prefix = "✅" if provider == current_provider else "⚪"
        buttons.append([InlineKeyboardButton(
            f"{prefix} {provider}",
            callback_data=f"provider:{provider}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Настройки", callback_data='settings')])
    return InlineKeyboardMarkup(buttons)


def ollama_models_menu(models: list, current_model: str, target: str):
    """Меню выбора модели Ollama
    
    target: 'text' или 'image' — для какого назначения выбираем модель.
    """
    import hashlib
    buttons = []
    # Telegram ограничивает callback_data до 64 байт
    # Формат: "ollama_model:text:model_name" - минимум ~20 символов
    # Формат с хешем: "ollama_model:text:hash:XXXXXXXX" - ~30 символов
    # Значит на имя модели остается ~44 символа, для хеша - ~34 символа
    max_callback_length = 64
    prefix_length = len(f"ollama_model:{target}:")
    max_model_name_length = max_callback_length - prefix_length - 5  # Запас на безопасность
    
    for model in models:
        prefix = "✅" if model == current_model else "⚪"
        # Обрезать длинные названия моделей для отображения
        display_name = model[:45] + "..." if len(model) > 45 else model
        
        # Если имя модели слишком длинное для callback_data, используем хеш
        if len(model) > max_model_name_length:
            # Используем хеш модели (8 символов) вместо полного имени
            model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
            callback_data = f"ollama_model:{target}:hash:{model_hash}"
        else:
            # Используем прямое имя модели для коротких имен
            callback_data = f"ollama_model:{target}:{model}"
        
        # Проверяем длину callback_data (на всякий случай)
        if len(callback_data) > max_callback_length:
            # Если все еще слишком длинный, используем хеш
            model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
            callback_data = f"ollama_model:{target}:hash:{model_hash}"
        
        buttons.append([InlineKeyboardButton(
            f"{prefix} {display_name}",
            callback_data=callback_data
        )])
    
    # Возврат в меню настроек
    buttons.append([InlineKeyboardButton("🔙 Назад к настройкам", callback_data='settings')])
    return InlineKeyboardMarkup(buttons)


def approve_menu(user_id: str):
    """Меню одобрения пользователя"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{user_id}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"decline:{user_id}")],
    ])


def user_management_menu():
    """Меню управления пользователями (короткое: только список + назад)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Список пользователей", callback_data='admin_users_page:1')],
        [InlineKeyboardButton("🔙 Админ-меню", callback_data='admin_menu')],
    ])


def knowledge_base_menu(knowledge_bases: list):
    """Меню управления базами знаний"""
    buttons = []
    for kb in knowledge_bases:
        # kb может быть как ORM-объектом, так и dict из backend API
        kb_id = getattr(kb, "id", None) or kb.get("id")
        kb_name = getattr(kb, "name", None) or kb.get("name")
        buttons.append([InlineKeyboardButton(
            f"📚 {kb_name}",
            callback_data=f"kb_select:{kb_id}"
        )])
    buttons.append([InlineKeyboardButton("➕ Создать базу знаний", callback_data='kb_create')])
    buttons.append([InlineKeyboardButton("🔙 Админ-меню", callback_data='admin_menu')])
    return InlineKeyboardMarkup(buttons)


def kb_actions_menu(kb_id: int, show_sources: bool = False):
    """Меню действий с базой знаний"""
    buttons = [
        [InlineKeyboardButton("📤 Загрузить документы", callback_data=f"kb_upload:{kb_id}")],
        [InlineKeyboardButton("🌐 Собрать вики по URL", callback_data=f"kb_wiki_crawl:{kb_id}")],
        [InlineKeyboardButton("📜 Журнал загрузок", callback_data=f"kb_import_log:{kb_id}")],
        [InlineKeyboardButton("📋 Список источников", callback_data=f"kb_sources:{kb_id}")],
        [InlineKeyboardButton("🗑️ Очистить базу", callback_data=f"kb_clear:{kb_id}")],
        [InlineKeyboardButton("❌ Удалить базу", callback_data=f"kb_delete:{kb_id}")],
        [InlineKeyboardButton("🔙 К базам знаний", callback_data='admin_kb')],
    ]
    return InlineKeyboardMarkup(buttons)


def document_type_menu():
    """Меню выбора типа документа для загрузки"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Markdown", callback_data='upload_type:markdown')],
        [InlineKeyboardButton("📝 Текстовый файл (TXT)", callback_data='upload_type:txt')],
        [InlineKeyboardButton("📑 Word", callback_data='upload_type:docx')],
        [InlineKeyboardButton("📊 Excel", callback_data='upload_type:xlsx')],
        [InlineKeyboardButton("📕 PDF", callback_data='upload_type:pdf')],
        [InlineKeyboardButton("📦 ZIP архив", callback_data='upload_type:zip')],
        [InlineKeyboardButton("🌐 Веб-страница", callback_data='upload_type:web')],
        [InlineKeyboardButton("🖼️ Изображение", callback_data='upload_type:image')],
        [InlineKeyboardButton("🔙 Админ-меню", callback_data='admin_menu')],
    ])


def confirm_menu(action: str, item_id: str = ""):
    """Меню подтверждения действия"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data=f"confirm:{action}:{item_id}")],
        [InlineKeyboardButton("❌ Нет", callback_data='cancel')],
    ])


def search_options_menu():
    """Меню опций поиска"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 В базе знаний", callback_data='search_kb')],
        [InlineKeyboardButton("🌐 В интернете", callback_data='search_web')],
        [InlineKeyboardButton("🤖 Спросить ИИ", callback_data='ask_ai')],
        [InlineKeyboardButton("🔙 Главное меню", callback_data='main_menu')],
    ])


def n8n_menu(public_url: str | None = None):
    """Меню управления интеграцией n8n"""
    buttons = [
        [InlineKeyboardButton("🔄 Проверить подключение", callback_data='n8n_ping')],
        [InlineKeyboardButton("🚀 Тестовое событие", callback_data='n8n_test_event')],
    ]
    if public_url:
        buttons.append([InlineKeyboardButton("🌐 Открыть n8n", url=public_url)])
    buttons.append([InlineKeyboardButton("🔙 Админ-меню", callback_data='admin_menu')])
    return InlineKeyboardMarkup(buttons)
