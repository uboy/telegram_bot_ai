from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Generator
import os

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


class AppSettings(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    asr_provider = Column(String(50), default='transformers')
    asr_model_name = Column(String(200), default='openai/whisper-small')
    asr_device = Column(String(50), default='')

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
            result = conn.execute("PRAGMA journal_mode").fetchone()
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

    except Exception as e:
        logger.warning(f"⚠️ Предупреждение при миграции: {e}")
        try:
            session.rollback()
        except:
            pass
    finally:
        session.close()

# Создать таблицы
Base.metadata.create_all(engine)

# Выполнить миграции
migrate_database()
