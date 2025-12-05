"""
Скрипт для просмотра содержимого базы данных знаний.
Позволяет проверить, что реально сохранено в базе для конкретного источника.
"""
import sys
import json
from database import Session, KnowledgeChunk, KnowledgeBase
from urllib.parse import unquote

def inspect_source(kb_id: int = None, source_path: str = None, kb_name: str = None):
    """
    Просмотреть содержимое базы данных для конкретного источника.
    
    Args:
        kb_id: ID базы знаний (если указан, kb_name игнорируется)
        source_path: Путь к источнику (URL или путь к файлу). Может быть частичным.
        kb_name: Имя базы знаний (используется, если kb_id не указан)
    """
    session = Session()
    
    try:
        # Определить ID базы знаний
        if kb_id:
            kb = session.query(KnowledgeBase).filter_by(id=kb_id).first()
        elif kb_name:
            kb = session.query(KnowledgeBase).filter_by(name=kb_name).first()
        else:
            print("❌ Необходимо указать либо kb_id, либо kb_name")
            print("\nДоступные базы знаний:")
            for kb in session.query(KnowledgeBase).all():
                print(f"  ID: {kb.id}, Имя: {kb.name}")
            return
        
        if not kb:
            print(f"❌ База знаний не найдена")
            return
        
        print(f"📚 База знаний: {kb.name} (ID: {kb.id})")
        print("=" * 80)
        
        # Найти все чанки
        query = session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb.id)
        
        if source_path:
            # Поиск по частичному совпадению source_path
            query = query.filter(KnowledgeChunk.source_path.like(f"%{source_path}%"))
        
        chunks = query.order_by(KnowledgeChunk.source_path, KnowledgeChunk.id).all()
        
        if not chunks:
            print(f"❌ Не найдено фрагментов")
            if source_path:
                print(f"   Поиск по: {source_path}")
            else:
                print("\nДоступные источники:")
                sources = (
                    session.query(KnowledgeChunk.source_path, KnowledgeChunk.source_type)
                    .filter_by(knowledge_base_id=kb.id)
                    .distinct()
                    .all()
                )
                for src_path, src_type in sources:
                    count = (
                        session.query(KnowledgeChunk)
                        .filter_by(knowledge_base_id=kb.id, source_path=src_path, source_type=src_type)
                        .count()
                    )
                    print(f"  {src_type}: {src_path} ({count} фрагментов)")
            return
        
        # Группировать по источникам
        sources_dict = {}
        for chunk in chunks:
            key = (chunk.source_path or "", chunk.source_type or "")
            if key not in sources_dict:
                sources_dict[key] = []
            sources_dict[key].append(chunk)
        
        print(f"\n✅ Найдено {len(chunks)} фрагментов в {len(sources_dict)} источников\n")
        
        for (src_path, src_type), src_chunks in sources_dict.items():
            print(f"\n{'=' * 80}")
            print(f"📄 Источник: {src_path}")
            print(f"   Тип: {src_type}")
            print(f"   Фрагментов: {len(src_chunks)}")
            print(f"{'=' * 80}\n")
            
            for idx, chunk in enumerate(src_chunks, 1):
                print(f"\n--- Фрагмент {idx}/{len(src_chunks)} (ID: {chunk.id}) ---")
                
                # Парсим метаданные
                metadata = {}
                if chunk.chunk_metadata:
                    try:
                        metadata = json.loads(chunk.chunk_metadata)
                    except:
                        pass
                
                if metadata:
                    print(f"Метаданные: {json.dumps(metadata, ensure_ascii=False, indent=2)}")
                
                # Показываем содержимое (первые 500 символов)
                content_preview = chunk.content[:500] if chunk.content else ""
                print(f"Содержимое (первые 500 символов):")
                print(f"{content_preview}...")
                if chunk.content and len(chunk.content) > 500:
                    print(f"\n... (всего {len(chunk.content)} символов)")
                
                print(f"\nДата создания: {chunk.created_at}")
                print("-" * 80)
        
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Просмотр содержимого базы данных знаний")
    parser.add_argument("--kb-id", type=int, help="ID базы знаний")
    parser.add_argument("--kb-name", type=str, help="Имя базы знаний")
    parser.add_argument("--source", type=str, help="Путь к источнику (URL или путь к файлу, может быть частичным)")
    
    args = parser.parse_args()
    
    if not args.kb_id and not args.kb_name:
        print("❌ Необходимо указать --kb-id или --kb-name")
        print("\nПримеры использования:")
        print("  python inspect_db.py --kb-name 'Моя база' --source 'https://gitee.com/mazurdenis/open-harmony/wikis/Sync'")
        print("  python inspect_db.py --kb-id 1 --source 'Sync&Build'")
        sys.exit(1)
    
    inspect_source(
        kb_id=args.kb_id,
        kb_name=args.kb_name,
        source_path=args.source
    )

