"""
Скрипт миграции базы данных для добавления недостающих колонок
"""
from sqlalchemy import text
from database import engine, Session

def migrate():
    """Выполнить миграцию базы данных"""
    session = Session()
    
    try:
        # Проверить и добавить колонку preferred_provider в таблицу users
        try:
            # Проверить, существует ли колонка
            result = session.execute(text("""
                SELECT COUNT(*) 
                FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'users' 
                AND COLUMN_NAME = 'preferred_provider'
            """))
            
            if result.scalar() == 0:
                # Добавить колонку
                session.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN preferred_provider VARCHAR(50) DEFAULT 'ollama'
                """))
                session.commit()
                print("✅ Колонка 'preferred_provider' добавлена в таблицу 'users'")
            else:
                print("ℹ️ Колонка 'preferred_provider' уже существует")
        
        except Exception as e:
            print(f"⚠️ Ошибка при проверке/добавлении колонки preferred_provider: {e}")
            # Попробовать добавить напрямую
            try:
                session.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN preferred_provider VARCHAR(50) DEFAULT 'ollama'
                """))
                session.commit()
                print("✅ Колонка 'preferred_provider' добавлена")
            except Exception as e2:
                print(f"❌ Не удалось добавить колонку: {e2}")
                session.rollback()
        
        # Проверить и создать таблицы для RAG системы, если их нет
        try:
            # Проверить таблицу knowledge_bases
            result = session.execute(text("""
                SELECT COUNT(*) 
                FROM information_schema.TABLES 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'knowledge_bases'
            """))
            
            if result.scalar() == 0:
                session.execute(text("""
                    CREATE TABLE knowledge_bases (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(100) UNIQUE NOT NULL,
                        description TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """))
                session.commit()
                print("✅ Таблица 'knowledge_bases' создана")
            else:
                print("ℹ️ Таблица 'knowledge_bases' уже существует")
        
        except Exception as e:
            print(f"⚠️ Ошибка при проверке/создании таблицы knowledge_bases: {e}")
            session.rollback()
        
        try:
            # Проверить таблицу knowledge_chunks
            result = session.execute(text("""
                SELECT COUNT(*) 
                FROM information_schema.TABLES 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'knowledge_chunks'
            """))
            
            if result.scalar() == 0:
                session.execute(text("""
                    CREATE TABLE knowledge_chunks (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        knowledge_base_id INT,
                        content TEXT,
                        chunk_metadata TEXT,
                        embedding TEXT,
                        source_type VARCHAR(50),
                        source_path VARCHAR(500),
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id)
                    )
                """))
                session.commit()
                print("✅ Таблица 'knowledge_chunks' создана")
            else:
                # Проверить, есть ли колонка chunk_metadata (вместо metadata)
                result = session.execute(text("""
                    SELECT COUNT(*) 
                    FROM information_schema.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                    AND TABLE_NAME = 'knowledge_chunks' 
                    AND COLUMN_NAME = 'chunk_metadata'
                """))
                
                if result.scalar() == 0:
                    # Проверить, есть ли старая колонка metadata
                    result2 = session.execute(text("""
                        SELECT COUNT(*) 
                        FROM information_schema.COLUMNS 
                        WHERE TABLE_SCHEMA = DATABASE() 
                        AND TABLE_NAME = 'knowledge_chunks' 
                        AND COLUMN_NAME = 'metadata'
                    """))
                    
                    if result2.scalar() > 0:
                        # Переименовать колонку
                        session.execute(text("""
                            ALTER TABLE knowledge_chunks 
                            CHANGE COLUMN metadata chunk_metadata TEXT
                        """))
                        session.commit()
                        print("✅ Колонка 'metadata' переименована в 'chunk_metadata'")
                    else:
                        # Добавить новую колонку
                        session.execute(text("""
                            ALTER TABLE knowledge_chunks 
                            ADD COLUMN chunk_metadata TEXT
                        """))
                        session.commit()
                        print("✅ Колонка 'chunk_metadata' добавлена")
                else:
                    print("ℹ️ Таблица 'knowledge_chunks' уже существует с правильной структурой")
        
        except Exception as e:
            print(f"⚠️ Ошибка при проверке/создании таблицы knowledge_chunks: {e}")
            session.rollback()
        
        print("\n✅ Миграция завершена!")
        
    except Exception as e:
        print(f"❌ Критическая ошибка миграции: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    migrate()

