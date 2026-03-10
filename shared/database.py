from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, text, Index, UniqueConstraint
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.exc import OperationalError
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Generator
import os
import time

from shared.logging_config import logger

# Поддержка как MySQL, так и SQLite
try:
    from shared.config import MYSQL_URL, DB_PATH
except ImportError:
    MYSQL_URL = None
    DB_PATH = None

Base = declarative_base()

class Message(Base):
    __tablename__ = 'messages'
    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20))
    user = Column(String(20))
    text = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(20), unique=True)
    username = Column(String(20))
    # Полное имя пользователя из Telegram (first_name + last_name)
    full_name = Column(String(100))
    # Номер телефона (если пользователь когда-либо предоставил его отдельно)
    phone = Column(String(32))
    approved = Column(Boolean, default=False)
    role = Column(String(10), default='user')
    preferred_provider = Column(String(50), default='ollama')  # Предпочитаемый провайдер ИИ
    preferred_model = Column(String(100), default='')  # Предпочитаемая модель (для Ollama)
    preferred_image_model = Column(String(100), default='')  # Предпочитаемая модель для изображений
    show_asr_metadata = Column(Boolean, default=True)  # Показывать тех. информацию ASR


class AppSettings(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    asr_provider = Column(String(50), default='transformers')
    asr_model_name = Column(String(200), default='openai/whisper-large-v3-turbo')
    asr_device = Column(String(50), default='')
    show_asr_metadata = Column(Boolean, default=True)  # Глобальная настройка тех. инфо ASR

class KnowledgeBase(Base):
    """База знаний"""
    __tablename__ = 'knowledge_bases'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True)
    description = Column(Text)
    settings = Column(Text)  # JSON строка с настройками БЗ
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Document(Base):
    """Документ в базе знаний (версионирование источников)"""
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_bases.id'))
    source_type = Column(String(50))
    source_path = Column(String(500))
    content_hash = Column(String(128))
    document_class = Column(String(50))
    language = Column(String(20))
    current_version = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DocumentVersion(Base):
    """Версия документа"""
    __tablename__ = 'document_versions'

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey('documents.id'))
    version = Column(Integer, default=1)
    content_hash = Column(String(128))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class KnowledgeChunk(Base):
    """Фрагмент знания"""
    __tablename__ = 'knowledge_chunks'
    
    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_bases.id'))
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=True)
    version = Column(Integer, default=1)
    content = Column(Text)
    chunk_metadata = Column(Text)  # JSON строка с метаданными
    metadata_json = Column(Text)  # JSON строка с метаданными (новый формат)
    chunk_hash = Column(String(128), nullable=True)
    chunk_no = Column(Integer, nullable=True)
    block_type = Column(String(50), nullable=True)
    parent_chunk_id = Column(String(128), nullable=True)
    prev_chunk_id = Column(String(128), nullable=True)
    next_chunk_id = Column(String(128), nullable=True)
    section_path_norm = Column(Text, nullable=True)
    page_no = Column(Integer, nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    token_count_est = Column(Integer, nullable=True)
    parser_profile = Column(String(120), nullable=True)
    parser_confidence = Column(Float, nullable=True)
    parser_warning = Column(Text, nullable=True)
    embedding = Column(Text)  # JSON строка с вектором
    source_type = Column(String(50))  # markdown, pdf, word, excel, web, image
    source_path = Column(String(500))
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeImportLog(Base):
    """Журнал загрузок в базы знаний"""
    __tablename__ = 'knowledge_import_logs'

    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_bases.id'))
    user_telegram_id = Column(String(20))
    username = Column(String(50))
    action_type = Column(String(50))  # document, web, wiki, image, archive и т.п.
    source_path = Column(String(500))  # Имя файла, URL или корень вики
    total_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Job(Base):
    """Асинхронные задачи (ингест/индексация)"""
    __tablename__ = 'jobs'

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=True)
    status = Column(String(20), default='pending')
    progress = Column(Integer, default=0)
    stage = Column(String(50))
    error_message = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# ── Chat Analytics models ──────────────────────────────────────────────

class ChatMessage(Base):
    """Полная история сообщений из Telegram supergroup topics"""
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False, index=True)
    thread_id = Column(Integer, nullable=True, index=True)
    message_id = Column(Integer, nullable=False)

    author_telegram_id = Column(String(20), nullable=True)
    author_username = Column(String(100), nullable=True)
    author_display_name = Column(String(200), nullable=True)

    text = Column(Text, nullable=True)
    message_link = Column(String(500), nullable=True)

    timestamp = Column(DateTime, nullable=False, index=True)
    is_bot_message = Column(Boolean, default=False)
    is_system_message = Column(Boolean, default=False)
    is_imported = Column(Boolean, default=False)
    import_source = Column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint('chat_id', 'message_id', name='uq_chat_message'),
        Index('ix_chat_messages_chat_thread', 'chat_id', 'thread_id'),
        Index('ix_chat_messages_chat_time', 'chat_id', 'timestamp'),
    )


class ChatAnalyticsConfig(Base):
    """Настройки аналитики для конкретного чата"""
    __tablename__ = 'chat_analytics_configs'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False, unique=True)
    chat_title = Column(String(200), nullable=True)

    collection_enabled = Column(Boolean, default=True)
    analysis_enabled = Column(Boolean, default=True)

    digest_cron = Column(String(100), nullable=True)
    digest_period_hours = Column(Integer, default=168)
    digest_timezone = Column(String(50), default='UTC')

    delivery_chat_id = Column(String(20), nullable=True)
    delivery_thread_id = Column(Integer, nullable=True)
    delivery_to_admins = Column(Boolean, default=False)

    configured_by = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class ChatDigest(Base):
    """Сгенерированный дайджест"""
    __tablename__ = 'chat_digests'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False, index=True)

    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    summary_text = Column(Text, nullable=True)
    theme_count = Column(Integer, default=0)
    total_messages_analyzed = Column(Integer, default=0)

    generation_time_sec = Column(Integer, nullable=True)
    llm_model_used = Column(String(100), nullable=True)
    status = Column(String(20), default='pending')
    error_message = Column(Text, nullable=True)

    delivered = Column(Boolean, default=False)
    delivered_at = Column(DateTime, nullable=True)
    delivered_message_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    themes = relationship("ChatDigestTheme", back_populates="digest",
                          cascade="all, delete-orphan")


class ChatDigestTheme(Base):
    """Тема внутри дайджеста"""
    __tablename__ = 'chat_digest_themes'

    id = Column(Integer, primary_key=True)
    digest_id = Column(Integer, ForeignKey('chat_digests.id'), nullable=False)

    emoji = Column(String(10), nullable=True)
    title = Column(String(300), nullable=False)
    summary = Column(Text, nullable=False)

    related_thread_ids = Column(Text, nullable=True)
    key_message_links = Column(Text, nullable=True)
    main_participants = Column(Text, nullable=True)
    message_count = Column(Integer, default=0)

    sort_order = Column(Integer, default=0)

    digest = relationship("ChatDigest", back_populates="themes")


class ChatMessageEmbedding(Base):
    """Кеш эмбеддингов для сообщений чатов"""
    __tablename__ = 'chat_message_embeddings'

    id = Column(Integer, primary_key=True)
    chat_message_id = Column(Integer, ForeignKey('chat_messages.id'),
                             nullable=False, unique=True)
    embedding = Column(Text, nullable=False)
    model_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChatImportLog(Base):
    """Лог импорта истории чата"""
    __tablename__ = 'chat_import_logs'

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(20), nullable=False)
    user_telegram_id = Column(String(20), nullable=True)
    source_filename = Column(String(500), nullable=True)
    source_format = Column(String(50), nullable=True)
    messages_imported = Column(Integer, default=0)
    messages_skipped = Column(Integer, default=0)
    status = Column(String(20), default='pending')
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AIConversation(Base):
    """Сессия диалога пользователя с ИИ в режиме direct AI."""
    __tablename__ = 'ai_conversations'

    id = Column(Integer, primary_key=True)
    user_telegram_id = Column(String(20), nullable=False, index=True)
    provider_name = Column(String(50), nullable=True)
    model_name = Column(String(120), nullable=True)
    status = Column(String(20), default='active', nullable=False)
    title = Column(String(200), nullable=True)
    summary_text = Column(Text, nullable=True)
    summary_version = Column(Integer, default=1, nullable=False)
    last_activity_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index('ix_ai_conversations_user_activity', 'user_telegram_id', 'last_activity_at'),
        Index('ix_ai_conversations_status_updated', 'status', 'updated_at'),
    )


class AIConversationTurn(Base):
    """Реплика внутри AIConversation."""
    __tablename__ = 'ai_conversation_turns'

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('ai_conversations.id'), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)  # user|assistant|system
    content = Column(Text, nullable=False)
    content_chars = Column(Integer, default=0, nullable=False)
    content_tokens_est = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint('conversation_id', 'turn_index', name='uq_ai_conv_turn_index'),
        Index('ix_ai_conv_turns_conv_created', 'conversation_id', 'created_at'),
    )


class AIRequestMetric(Base):
    """Метрики LLM запросов для latency prediction и наблюдаемости."""
    __tablename__ = 'ai_request_metrics'

    id = Column(Integer, primary_key=True)
    request_id = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    feature = Column(String(64), nullable=True)  # ask_ai_text, ask_ai_voice, rag_query, ...
    user_telegram_id = Column(String(20), nullable=True, index=True)
    conversation_id = Column(Integer, ForeignKey('ai_conversations.id'), nullable=True, index=True)
    provider_name = Column(String(50), nullable=True)
    model_name = Column(String(120), nullable=True)
    request_kind = Column(String(20), nullable=False, default='text')  # text|multimodal

    prompt_chars = Column(Integer, default=0, nullable=False)
    prompt_tokens_est = Column(Integer, default=0, nullable=False)
    context_chars = Column(Integer, default=0, nullable=False)
    context_tokens_est = Column(Integer, default=0, nullable=False)
    history_turns_used = Column(Integer, default=0, nullable=False)

    predicted_latency_ms = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=False, default=0)

    response_chars = Column(Integer, default=0, nullable=False)
    response_tokens_est = Column(Integer, default=0, nullable=False)

    status = Column(String(20), nullable=False, default='ok')  # ok|error|timeout
    error_type = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index('ix_ai_metrics_provider_model_feature_time', 'provider_name', 'model_name', 'feature', 'created_at'),
        Index('ix_ai_metrics_status_time', 'status', 'created_at'),
    )


class RetrievalQueryLog(Base):
    """Лог RAG-запросов для диагностики retrieval pipeline."""
    __tablename__ = 'retrieval_query_logs'

    id = Column(Integer, primary_key=True)
    request_id = Column(String(64), nullable=False, unique=True, index=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_bases.id'), nullable=True, index=True)
    query = Column(Text, nullable=False)
    intent = Column(String(32), nullable=True)
    hints_json = Column(Text, nullable=True)
    filters_json = Column(Text, nullable=True)
    total_candidates = Column(Integer, default=0, nullable=False)
    total_selected = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, default=0, nullable=False)
    backend_name = Column(String(32), nullable=True)
    degraded_mode = Column(Boolean, default=False, nullable=False)
    degraded_reason = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    __table_args__ = (
        Index('ix_retrieval_query_kb_created', 'knowledge_base_id', 'created_at'),
    )


class RetrievalCandidateLog(Base):
    """Топ кандидатов retrieval для одного запроса."""
    __tablename__ = 'retrieval_candidate_logs'

    id = Column(Integer, primary_key=True)
    request_id = Column(String(64), ForeignKey('retrieval_query_logs.request_id'), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    source_path = Column(String(500), nullable=True)
    source_type = Column(String(50), nullable=True)
    distance = Column(String(32), nullable=True)
    rerank_score = Column(String(32), nullable=True)
    origin = Column(String(32), nullable=True)
    channel = Column(String(32), nullable=True)
    channel_rank = Column(Integer, nullable=True)
    fusion_rank = Column(Integer, nullable=True)
    fusion_score = Column(String(32), nullable=True)
    rerank_delta = Column(String(32), nullable=True)
    metadata_json = Column(Text, nullable=True)
    content_preview = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('ix_retrieval_candidate_request_rank', 'request_id', 'rank'),
    )


class IndexOutboxEvent(Base):
    """Outbox-событие синхронизации индекса (SQL -> векторный backend)."""
    __tablename__ = 'index_outbox_events'

    id = Column(Integer, primary_key=True)
    event_id = Column(String(64), nullable=False, unique=True, index=True)
    idempotency_key = Column(String(180), nullable=False, unique=True, index=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_bases.id'), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=True, index=True)
    version = Column(Integer, nullable=True)
    operation = Column(String(32), nullable=False)  # UPSERT|DELETE_SOURCE|DELETE_KB
    payload_json = Column(Text, nullable=True)
    status = Column(String(20), default='pending', nullable=False, index=True)  # pending|processing|processed|dead
    attempt_count = Column(Integer, default=0, nullable=False)
    available_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    locked_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    __table_args__ = (
        Index('ix_index_outbox_status_available', 'status', 'available_at'),
    )


class IndexSyncAudit(Base):
    """Аудит дрейфа между SQL и векторным индексом."""
    __tablename__ = 'index_sync_audit'

    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_bases.id'), nullable=False, index=True)
    expected_active_chunks = Column(Integer, default=0, nullable=False)
    indexed_chunks = Column(Integer, default=0, nullable=False)
    drift_ratio = Column(Float, default=0.0, nullable=False)
    status = Column(String(20), default='ok', nullable=False)  # ok|warning|critical
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    __table_args__ = (
        Index('ix_index_sync_audit_kb_created', 'knowledge_base_id', 'created_at'),
    )


class RAGEvalRun(Base):
    """Запуск quality benchmark для retrieval/generation."""
    __tablename__ = 'rag_eval_runs'

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    suite_name = Column(String(80), nullable=False)
    baseline_run_id = Column(String(64), nullable=True)
    status = Column(String(20), default='queued', nullable=False)  # queued|running|completed|failed
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    metrics_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)


class RAGEvalResult(Base):
    """Результат benchmark по срезам/метрикам."""
    __tablename__ = 'rag_eval_results'

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey('rag_eval_runs.run_id'), nullable=False, index=True)
    slice_name = Column(String(64), nullable=False, index=True)
    metric_name = Column(String(64), nullable=False, index=True)
    metric_value = Column(Float, nullable=False, default=0.0)
    threshold_value = Column(Float, nullable=True)
    passed = Column(Boolean, default=False, nullable=False)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    __table_args__ = (
        Index('ix_rag_eval_results_run_slice_metric', 'run_id', 'slice_name', 'metric_name'),
    )


class RetentionDeletionAudit(Base):
    """Аудит выполнения retention-политик и удаления данных."""
    __tablename__ = 'retention_deletion_audit'

    id = Column(Integer, primary_key=True)
    table_name = Column(String(80), nullable=False, index=True)
    policy_name = Column(String(80), nullable=False)
    rows_deleted = Column(Integer, default=0, nullable=False)
    deleted_before = Column(DateTime, nullable=True)
    status = Column(String(20), default='ok', nullable=False)  # ok|failed
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

# Определить, какую базу данных использовать
# Приоритет: MYSQL_URL > DB_PATH > SQLite по умолчанию
db_url = None

if MYSQL_URL:
    db_url = MYSQL_URL
    if DB_PATH:
        logger.warning(
            "DB_PATH указано одновременно с MYSQL_URL. Используется MySQL, DB_PATH проигнорирован."
        )
    try:
        # Если используется Docker и MYSQL_URL указывает на localhost, заменить на имя сервиса db
        if os.getenv("BOT_DATA_DIR") and ("localhost" in MYSQL_URL or "127.0.0.1" in MYSQL_URL):
            db_url = MYSQL_URL.replace("localhost", "db").replace("127.0.0.1", "db")
            logger.info("🗄️ Используется MySQL база данных (Docker: подключение к сервису db)")
        else:
            logger.info(
                f"🗄️ Используется MySQL база данных: "
                f"{MYSQL_URL.split('@')[-1] if '@' in MYSQL_URL else MYSQL_URL}"
            )
    except (UnicodeEncodeError, UnicodeError):
        logger.info("[MySQL] Используется MySQL база данных")

elif DB_PATH:
    # Использовать SQLite (локальная база данных) - явно указан путь
    # Убедиться, что путь существует для сохранения данных
    db_dir = os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else "."
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    db_url = f"sqlite:///{DB_PATH}"
    try:
        logger.info(f"📁 Используется локальная база данных SQLite: {DB_PATH}")
        logger.info(f"   Директория: {db_dir}")
    except (UnicodeEncodeError, UnicodeError):
        logger.info(f"[SQLite] Используется локальная база данных: {DB_PATH}")
elif not MYSQL_URL:
    # Если не указан ни DB_PATH, ни MYSQL_URL, использовать SQLite по умолчанию в папке data/db
    default_db_path = os.path.join(os.getenv("BOT_DATA_DIR", "/app/data"), "db", "bot_database.db")
    db_dir = os.path.dirname(default_db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    db_url = f"sqlite:///{default_db_path}"
    try:
        logger.warning(f"⚠️ MYSQL_URL и DB_PATH не указаны, используется SQLite по умолчанию: {default_db_path}")
        logger.info(f"   Директория: {db_dir}")
    except (UnicodeEncodeError, UnicodeError):
        logger.warning(f"[WARNING] MYSQL_URL и DB_PATH не указаны, используется SQLite по умолчанию: {default_db_path}")
if not db_url:
    raise RuntimeError("Не удалось определить URL базы данных.")

# Логируем финальный URL для отладки (без пароля)
safe_url = db_url
if '@' in safe_url and '://' in safe_url:
    try:
        parts = safe_url.split('://')
        if len(parts) > 1:
            auth_part = parts[1].split('@')[0] if '@' in parts[1] else ''
            if ':' in auth_part:
                user = auth_part.split(':')[0]
                safe_url = f"{parts[0]}://{user}:***@{parts[1].split('@')[-1]}" if '@' in parts[1] else safe_url
    except:
        pass
logger.info(f"🔗 URL базы данных: {safe_url}")

# Настройки для SQLite: WAL режим и таймауты для предотвращения блокировок
connect_args = {}
if 'sqlite' in db_url:
    # Включить WAL (Write-Ahead Logging) режим для лучшей производительности и параллелизма
    connect_args = {
        'check_same_thread': False,  # Разрешить использование из разных потоков
        'timeout': 60,  # Таймаут ожидания блокировки на уровне sqlite3 (60 секунд)
    }

engine = create_engine(
    db_url, 
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,  # Проверять соединения перед использованием
    pool_recycle=3600,  # Переиспользовать соединения каждый час
)

# Включить WAL режим для SQLite после создания engine
if 'sqlite' in db_url:
    from sqlalchemy import event
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        # Включить WAL режим (Write-Ahead Logging) для лучшего параллелизма
        cursor = dbapi_conn.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')  # Баланс между производительностью и надежностью
        cursor.execute('PRAGMA busy_timeout=60000')  # 60 секунд таймаут (60000 мс) - должен совпадать с timeout
        cursor.execute('PRAGMA wal_autocheckpoint=1000')  # Автоматический checkpoint каждые 1000 страниц
        # Временно для диагностики: принудительный checkpoint
        # cursor.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        cursor.close()
    
    event.listen(engine, "connect", _set_sqlite_pragma)
    
    # Также проверить и включить WAL при первом подключении
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA journal_mode")).fetchone()
            if result and result[0].upper() != 'WAL':
                logger.warning(f"SQLite WAL режим не включен, текущий режим: {result[0]}")
            else:
                logger.info("✅ SQLite WAL режим включен")
    except Exception as e:
        logger.warning(f"Не удалось проверить WAL режим: {e}")

Session = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Контекстный менеджер для создания и закрытия сессии БД
    
    Использование:
        with get_session() as session:
            # работа с session
            session.add(...)
            # commit выполнится автоматически при выходе из with
    """
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def migrate_database():
    """Автоматическая миграция базы данных"""
    from sqlalchemy import text, inspect
    session = Session()
    # Определить тип базы данных по URL
    is_sqlite = 'sqlite' in str(engine.url)
    
    try:
        inspector = inspect(engine)
        
        # Проверить и добавить колонку preferred_provider
        try:
            if 'users' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('users')]
                if 'preferred_provider' not in columns:
                    session.execute(text("""
                        ALTER TABLE users 
                        ADD COLUMN preferred_provider VARCHAR(50) DEFAULT 'ollama'
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'preferred_provider'")
        except Exception as e:
            # Колонка уже существует или другая ошибка
            try:
                session.rollback()
            except:
                pass
        
        # Проверить и добавить колонку preferred_model
        try:
            if 'users' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('users')]
                if 'preferred_model' not in columns:
                    session.execute(text("""
                        ALTER TABLE users 
                        ADD COLUMN preferred_model VARCHAR(100) DEFAULT ''
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'preferred_model'")
        except Exception as e:
            # Колонка уже существует или другая ошибка
            try:
                session.rollback()
            except:
                pass

        # Проверить и добавить колонку preferred_image_model
        try:
            if 'users' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('users')]
                if 'preferred_image_model' not in columns:
                    session.execute(text("""
                        ALTER TABLE users 
                        ADD COLUMN preferred_image_model VARCHAR(100) DEFAULT ''
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'preferred_image_model'")
        except Exception as e:
            # Колонка уже существует или другая ошибка
            try:
                session.rollback()
            except:
                pass

        # Проверить и добавить колонку full_name
        try:
            if 'users' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('users')]
                if 'full_name' not in columns:
                    session.execute(text("""
                        ALTER TABLE users
                        ADD COLUMN full_name VARCHAR(100)
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'full_name'")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Проверить и добавить колонку settings в knowledge_bases
        try:
            if 'knowledge_bases' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('knowledge_bases')]
                if 'settings' not in columns:
                    session.execute(text("""
                        ALTER TABLE knowledge_bases
                        ADD COLUMN settings TEXT
                    """))
                    session.commit()
                    logger.info("? Миграция: добавлена колонка 'settings' в knowledge_bases")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Проверить и добавить колонку phone
        try:
            if 'users' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('users')]
                if 'phone' not in columns:
                    session.execute(text("""
                        ALTER TABLE users
                        ADD COLUMN phone VARCHAR(32)
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'phone'")
                if 'show_asr_metadata' not in columns:
                    session.execute(text("""
                        ALTER TABLE users
                        ADD COLUMN show_asr_metadata BOOLEAN DEFAULT 1
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'show_asr_metadata' в 'users'")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Проверить и добавить колонку show_asr_metadata в app_settings
        try:
            if 'app_settings' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('app_settings')]
                if 'show_asr_metadata' not in columns:
                    session.execute(text("""
                        ALTER TABLE app_settings
                        ADD COLUMN show_asr_metadata BOOLEAN DEFAULT 1
                    """))
                    session.commit()
                    logger.info("✅ Миграция: добавлена колонка 'show_asr_metadata' в 'app_settings'")
        except Exception:
            try:
                session.rollback()
            except:
                pass
        
        # Проверить и переименовать metadata в chunk_metadata для knowledge_chunks
        try:
            if 'knowledge_chunks' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('knowledge_chunks')]
                if 'chunk_metadata' not in columns:
                    if 'metadata' in columns:
                        # Есть старая колонка metadata, нужно переименовать
                        if is_sqlite:
                            # SQLite не поддерживает переименование напрямую
                            # Добавим новую колонку и скопируем данные
                            session.execute(text("""
                                ALTER TABLE knowledge_chunks 
                                ADD COLUMN chunk_metadata TEXT
                            """))
                            session.execute(text("""
                                UPDATE knowledge_chunks 
                                SET chunk_metadata = metadata 
                                WHERE metadata IS NOT NULL
                            """))
                            # Старую колонку оставим (SQLite не поддерживает удаление колонок легко)
                        else:
                            # MySQL - переименовать колонку
                            session.execute(text("""
                                ALTER TABLE knowledge_chunks 
                                CHANGE COLUMN metadata chunk_metadata TEXT
                            """))
                        session.commit()
                        logger.info("✅ Миграция: переименована колонка 'metadata' в 'chunk_metadata'")
                    else:
                        # Колонки metadata нет, просто добавим chunk_metadata
                        session.execute(text("""
                            ALTER TABLE knowledge_chunks 
                            ADD COLUMN chunk_metadata TEXT
                        """))
                        session.commit()
                        logger.info("✅ Миграция: добавлена колонка 'chunk_metadata'")
        except Exception as e:
            # Игнорировать ошибки миграции (колонка уже существует)
            try:
                session.rollback()
            except:
                pass

        # Create jobs table if missing
        try:
            if 'jobs' not in inspector.get_table_names():
                session.execute(text("""
                    CREATE TABLE jobs (
                        id INTEGER PRIMARY KEY,
                        document_id INTEGER,
                        status VARCHAR(20) DEFAULT 'pending',
                        progress INTEGER DEFAULT 0,
                        stage VARCHAR(50),
                        error_message TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                session.commit()
                logger.info("✅ Migration: created table 'jobs'")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Add new columns for knowledge_chunks (versioning)
        try:
            if 'knowledge_chunks' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('knowledge_chunks')]
                if 'document_id' not in columns:
                    session.execute(text("""
                        ALTER TABLE knowledge_chunks
                        ADD COLUMN document_id INTEGER
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'document_id' to knowledge_chunks")
                if 'version' not in columns:
                    session.execute(text("""
                        ALTER TABLE knowledge_chunks
                        ADD COLUMN version INTEGER DEFAULT 1
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'version' to knowledge_chunks")
                if 'metadata_json' not in columns:
                    session.execute(text("""
                        ALTER TABLE knowledge_chunks
                        ADD COLUMN metadata_json TEXT
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'metadata_json' to knowledge_chunks")
                if 'is_deleted' not in columns:
                    session.execute(text("""
                        ALTER TABLE knowledge_chunks
                        ADD COLUMN is_deleted BOOLEAN DEFAULT 0
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'is_deleted' to knowledge_chunks")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Add canonical chunk contract columns for knowledge_chunks
        try:
            if 'knowledge_chunks' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('knowledge_chunks')]
                canonical_columns = [
                    ('chunk_hash', "ALTER TABLE knowledge_chunks ADD COLUMN chunk_hash VARCHAR(128)"),
                    ('chunk_no', "ALTER TABLE knowledge_chunks ADD COLUMN chunk_no INTEGER"),
                    ('block_type', "ALTER TABLE knowledge_chunks ADD COLUMN block_type VARCHAR(50)"),
                    ('parent_chunk_id', "ALTER TABLE knowledge_chunks ADD COLUMN parent_chunk_id VARCHAR(128)"),
                    ('prev_chunk_id', "ALTER TABLE knowledge_chunks ADD COLUMN prev_chunk_id VARCHAR(128)"),
                    ('next_chunk_id', "ALTER TABLE knowledge_chunks ADD COLUMN next_chunk_id VARCHAR(128)"),
                    ('section_path_norm', "ALTER TABLE knowledge_chunks ADD COLUMN section_path_norm TEXT"),
                    ('page_no', "ALTER TABLE knowledge_chunks ADD COLUMN page_no INTEGER"),
                    ('char_start', "ALTER TABLE knowledge_chunks ADD COLUMN char_start INTEGER"),
                    ('char_end', "ALTER TABLE knowledge_chunks ADD COLUMN char_end INTEGER"),
                    ('token_count_est', "ALTER TABLE knowledge_chunks ADD COLUMN token_count_est INTEGER"),
                    ('parser_profile', "ALTER TABLE knowledge_chunks ADD COLUMN parser_profile VARCHAR(120)"),
                    ('parser_confidence', "ALTER TABLE knowledge_chunks ADD COLUMN parser_confidence FLOAT"),
                    ('parser_warning', "ALTER TABLE knowledge_chunks ADD COLUMN parser_warning TEXT"),
                ]
                for column_name, ddl in canonical_columns:
                    if column_name in columns:
                        continue
                    session.execute(text(ddl))
                    session.commit()
                    columns.append(column_name)
                    logger.info("✅ Migration: added '%s' to knowledge_chunks", column_name)
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Ensure app_settings has a default row
        try:
            if 'app_settings' in inspector.get_table_names():
                count = session.execute(text("SELECT COUNT(*) FROM app_settings")).scalar()
                if not count:
                    session.execute(text("""
                        INSERT INTO app_settings (asr_provider, asr_model_name, asr_device)
                        VALUES ('transformers', 'openai/whisper-small', '')
                    """))
                    session.commit()
                    logger.info("✅ Migration: created default row in app_settings")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Extend retrieval_query_logs with degraded mode diagnostics
        try:
            if 'retrieval_query_logs' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('retrieval_query_logs')]
                if 'degraded_mode' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_query_logs
                        ADD COLUMN degraded_mode BOOLEAN DEFAULT 0
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'degraded_mode' to retrieval_query_logs")
                if 'degraded_reason' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_query_logs
                        ADD COLUMN degraded_reason VARCHAR(120)
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'degraded_reason' to retrieval_query_logs")
        except Exception:
            try:
                session.rollback()
            except:
                pass

        # Extend retrieval_candidate_logs for channel/fusion/rerank diagnostics
        try:
            if 'retrieval_candidate_logs' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('retrieval_candidate_logs')]
                if 'channel' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_candidate_logs
                        ADD COLUMN channel VARCHAR(32)
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'channel' to retrieval_candidate_logs")
                if 'channel_rank' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_candidate_logs
                        ADD COLUMN channel_rank INTEGER
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'channel_rank' to retrieval_candidate_logs")
                if 'fusion_rank' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_candidate_logs
                        ADD COLUMN fusion_rank INTEGER
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'fusion_rank' to retrieval_candidate_logs")
                if 'fusion_score' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_candidate_logs
                        ADD COLUMN fusion_score VARCHAR(32)
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'fusion_score' to retrieval_candidate_logs")
                if 'rerank_delta' not in columns:
                    session.execute(text("""
                        ALTER TABLE retrieval_candidate_logs
                        ADD COLUMN rerank_delta VARCHAR(32)
                    """))
                    session.commit()
                    logger.info("✅ Migration: added 'rerank_delta' to retrieval_candidate_logs")
        except Exception:
            try:
                session.rollback()
            except:
                pass

    except Exception as e:
        logger.warning(f"⚠️ Предупреждение при миграции: {e}")
        try:
            session.rollback()
        except:
            pass
    finally:
        session.close()

def create_all_tables_with_race_tolerance(max_attempts: int = 3, sleep_sec: float = 0.2) -> None:
    """Create all SQLAlchemy tables, tolerating SQLite startup races.

    When backend and bot start simultaneously against the same SQLite file,
    both processes may pass table-existence check and race on CREATE TABLE.
    In that case SQLAlchemy can surface "table ... already exists".
    """
    is_sqlite = 'sqlite' in str(engine.url)
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError as exc:
            message = str(exc).lower()
            race_already_exists = is_sqlite and "already exists" in message
            if not race_already_exists:
                raise
            if attempt >= attempts:
                logger.warning(
                    "SQLite create_all race detected on startup (already exists). "
                    "Proceeding with existing schema after %s attempt(s).",
                    attempt,
                )
                return
            logger.warning(
                "SQLite create_all race detected on startup (attempt %s/%s). Retrying...",
                attempt,
                attempts,
            )
            time.sleep(max(0.0, float(sleep_sec)))

# Создать таблицы (устойчиво к гонке одновременного старта bot/backend на SQLite)
create_all_tables_with_race_tolerance()

# Выполнить миграции
migrate_database()
