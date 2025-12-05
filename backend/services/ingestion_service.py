"""
Сервис загрузки документов/веб-страниц/вики в базы знаний.

На первом этапе использует существующую синхронную логику rag_system и document_loader_manager,
перенесённую из кода Telegram-бота.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List
import hashlib
import os
import tempfile
import zipfile

from sqlalchemy.orm import Session

from shared.database import KnowledgeImportLog  # type: ignore
from shared.rag_system import rag_system  # type: ignore
from shared.document_loaders import document_loader_manager  # type: ignore
from shared.utils import detect_language  # type: ignore
from shared.wiki_scraper import crawl_wiki_to_kb  # type: ignore
from shared.wiki_git_loader import load_wiki_from_git, load_wiki_from_zip  # type: ignore
from shared.image_processor import image_processor  # type: ignore


class IngestionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ingest_web_page(self, kb_id: int, url: str, telegram_id: str | None, username: str | None) -> Dict[str, Any]:
        """Загрузить одну веб-страницу в базу знаний."""
        chunks = document_loader_manager.load_document(url, "web")

        # Удалить старые фрагменты этой страницы (обновление версии)
        rag_system.delete_chunks_by_source_exact(
            knowledge_base_id=kb_id,
            source_type="web",
            source_path=url,
        )

        existing_logs = (
            self.db.query(KnowledgeImportLog)
            .filter_by(knowledge_base_id=kb_id, source_path=url)
            .count()
        )
        doc_version = existing_logs + 1
        source_updated_at = datetime.now(timezone.utc).isoformat()

        added = 0
        for chunk in chunks:
            content = chunk.get("content", "")
            base_meta = dict(chunk.get("metadata") or {})
            base_meta.setdefault("title", chunk.get("title") or url)
            base_meta["language"] = detect_language(content) if content else "ru"
            base_meta["doc_version"] = doc_version
            base_meta["source_updated_at"] = source_updated_at

            rag_system.add_chunk(
                knowledge_base_id=kb_id,
                content=content,
                source_type="web",
                source_path=url,
                metadata=base_meta,
            )
            added += 1

        # Записать в журнал загрузок
        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=telegram_id or "",
            username=username or telegram_id or "",
            action_type="web",
            source_path=url,
            total_chunks=added,
        )
        self.db.add(log)
        self.db.commit()

        return {
            "kb_id": kb_id,
            "url": url,
            "chunks_added": added,
            "doc_version": doc_version,
            "source_updated_at": source_updated_at,
        }

    def ingest_wiki_crawl(
        self,
        kb_id: int,
        wiki_url: str,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        """Рекурсивно обойти wiki-раздел и загрузить страницы в БЗ."""
        stats = crawl_wiki_to_kb(base_url=wiki_url, knowledge_base_id=kb_id, max_pages=500)
        deleted = stats.get("deleted_chunks", 0)
        pages = stats.get("pages_processed", 0)
        added = stats.get("chunks_added", 0)
        wiki_root = stats.get("wiki_root", wiki_url)

        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=telegram_id or "",
            username=username or telegram_id or "",
            action_type="wiki",
            source_path=wiki_root,
            total_chunks=added,
        )
        self.db.add(log)
        self.db.commit()

        return {
            "deleted_chunks": deleted,
            "pages_processed": pages,
            "chunks_added": added,
            "wiki_root": wiki_root,
        }

    def ingest_wiki_git(
        self,
        kb_id: int,
        wiki_url: str,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        """Загрузить вики из Git-репозитория (через wiki_git_loader)."""
        stats = load_wiki_from_git(wiki_url, kb_id)
        deleted = stats.get("deleted_chunks", 0)
        files = stats.get("files_processed", 0)
        added = stats.get("chunks_added", 0)
        wiki_root = stats.get("wiki_root", wiki_url)

        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=telegram_id or "",
            username=username or telegram_id or "",
            action_type="wiki_git",
            source_path=wiki_root,
            total_chunks=added,
        )
        self.db.add(log)
        self.db.commit()

        return {
            "deleted_chunks": deleted,
            "files_processed": files,
            "chunks_added": added,
            "wiki_root": wiki_root,
        }

    def ingest_wiki_zip(
        self,
        kb_id: int,
        wiki_url: str,
        zip_path: str,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        """Загрузить вики из ZIP архива (через wiki_git_loader.load_wiki_from_zip)."""
        stats = load_wiki_from_zip(zip_path, wiki_url, kb_id)
        deleted = stats.get("deleted_chunks", 0)
        files = stats.get("files_processed", 0)
        added = stats.get("chunks_added", 0)
        wiki_root = stats.get("wiki_root", wiki_url)
        processed_files: List[Dict[str, Any]] = stats.get("processed_files", [])

        # Записываем каждый файл в журнал загрузок
        for file_info in processed_files:
            log = KnowledgeImportLog(
                knowledge_base_id=kb_id,
                user_telegram_id=telegram_id or "",
                username=username or telegram_id or "",
                action_type="archive",
                source_path=file_info.get("wiki_url", ""),
                total_chunks=file_info.get("chunks", 0),
            )
            self.db.add(log)
        self.db.commit()

        return {
            "deleted_chunks": deleted,
            "files_processed": files,
            "chunks_added": added,
            "wiki_root": wiki_root,
            "processed_files": processed_files,
        }

    # === Документы и архивы ===

    def ingest_document_or_archive(
        self,
        kb_id: int,
        file_path: str,
        file_name: str,
        file_type: str | None,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        """
        Загрузить одиночный документ или архив (ZIP) в базу знаний.

        Логика перенесена из load_document_to_kb в боте.
        """
        file_type = (file_type or "").lower()

        # Определить пользователя для журнала загрузок
        tg_id = telegram_id or ""
        username_val = username or tg_id

        # Поддержка архивов (zip)
        per_file_stats: List[Dict[str, Any]] = []
        total_chunks = 0

        if file_type == "zip":
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    # Пропустить каталоги
                    if name.endswith("/"):
                        continue
                    # Пропустить файлы .keep и другие служебные файлы
                    if ".keep" in name.lower() or name.endswith(".keep"):
                        continue
                    inner_ext = os.path.splitext(name)[1].lstrip(".").lower()
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
                        source_type=inner_ext or "unknown",
                        source_path=source_path,
                    )
                    try:
                        chunks = document_loader_manager.load_document(inner_path, inner_ext or None)
                        # Фильтруем пустые чанки (менее 10 символов)
                        chunks = [
                            chunk
                            for chunk in chunks
                            if chunk.get("content", "").strip()
                            and len(chunk.get("content", "").strip()) > 10
                        ]
                    except Exception:  # noqa: BLE001
                        chunks = []
                    added = 0
                    # Версия документа — порядковый номер загрузки этого источника
                    existing_logs = (
                        self.db.query(KnowledgeImportLog)
                        .filter_by(knowledge_base_id=kb_id, source_path=source_path)
                        .count()
                    )
                    doc_version = existing_logs + 1
                    source_updated_at = datetime.now(timezone.utc).isoformat()

                    for chunk in chunks:
                        content = chunk.get("content", "")
                        base_meta = dict(chunk.get("metadata") or {})
                        base_meta.setdefault("title", chunk.get("title") or name)
                        base_meta["language"] = detect_language(content) if content else "ru"
                        base_meta["doc_hash"] = doc_hash
                        base_meta["doc_version"] = doc_version
                        base_meta["source_updated_at"] = source_updated_at

                        rag_system.add_chunk(
                            knowledge_base_id=kb_id,
                            content=content,
                            source_type=inner_ext or "unknown",
                            source_path=source_path,
                            metadata=base_meta,
                        )
                        added += 1
                    total_chunks += added
                    per_file_stats.append(
                        {
                            "name": name,
                            "chunks_added": added,
                        }
                    )
                    # Записать в журнал загрузок для каждого файла
                    log = KnowledgeImportLog(
                        knowledge_base_id=kb_id,
                        user_telegram_id=tg_id,
                        username=username_val,
                        action_type="archive",
                        source_path=source_path,
                        total_chunks=added,
                    )
                    self.db.add(log)
                    try:
                        os.remove(inner_path)
                    except OSError:
                        pass
                self.db.commit()

            return {
                "mode": "archive",
                "kb_id": kb_id,
                "file_name": file_name,
                "total_chunks": total_chunks,
                "files": per_file_stats,
            }

        # Обычный одиночный документ
        with open(file_path, "rb") as f:
            data = f.read()
        doc_hash = hashlib.sha256(data).hexdigest()
        source_path = file_name or ""

        # Удалить старые фрагменты этого документа (если загружается новая версия)
        rag_system.delete_chunks_by_source_exact(
            knowledge_base_id=kb_id,
            source_type=file_type or "unknown",
            source_path=source_path,
        )

        chunks = document_loader_manager.load_document(file_path, file_type or None)

        existing_logs = (
            self.db.query(KnowledgeImportLog)
            .filter_by(knowledge_base_id=kb_id, source_path=source_path)
            .count()
        )
        doc_version = existing_logs + 1
        source_updated_at = datetime.now(timezone.utc).isoformat()

        added = 0
        for chunk in chunks:
            content = chunk.get("content", "")
            base_meta = dict(chunk.get("metadata") or {})
            base_meta.setdefault("title", chunk.get("title") or source_path)
            base_meta["language"] = detect_language(content) if content else "ru"
            base_meta["doc_hash"] = doc_hash
            base_meta["doc_version"] = doc_version
            base_meta["source_updated_at"] = source_updated_at

            rag_system.add_chunk(
                knowledge_base_id=kb_id,
                content=content,
                source_type=file_type or "unknown",
                source_path=source_path,
                metadata=base_meta,
            )
            added += 1

        # Записать в журнал загрузок
        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=tg_id,
            username=username_val,
            action_type="document",
            source_path=source_path,
            total_chunks=added,
        )
        self.db.add(log)
        self.db.commit()

        return {
            "mode": "document",
            "kb_id": kb_id,
            "file_name": file_name,
            "total_chunks": added,
            "doc_version": doc_version,
            "source_updated_at": source_updated_at,
        }

    # === Изображения ===

    def ingest_image(
        self,
        kb_id: int,
        file_path: str,
        file_id: str,
        telegram_id: str | None,
        username: str | None,
        model: str | None = None,
    ) -> Dict[str, Any]:
        """
        Обработать изображение через image_processor и сохранить как чанки RAG.
        """
        processed_text = image_processor.process_image_for_rag(
            file_path,
            model=model,
        )
        source_updated_at = datetime.now(timezone.utc).isoformat()
        source_path = f"photo_{file_id}.jpg"

        # Удалить старый фрагмент для этого изображения (если был)
        rag_system.delete_chunks_by_source_exact(
            knowledge_base_id=kb_id,
            source_type="image",
            source_path=source_path,
        )

        rag_system.add_chunk(
            knowledge_base_id=kb_id,
            content=processed_text,
            source_type="image",
            source_path=source_path,
            metadata={
                "type": "image",
                "file_id": file_id,
                "source_updated_at": source_updated_at,
            },
        )

        # Записать в журнал загрузок
        tg_id = telegram_id or ""
        username_val = username or tg_id
        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=tg_id,
            username=username_val,
            action_type="image",
            source_path=source_path,
            total_chunks=1,
        )
        self.db.add(log)
        self.db.commit()

        return {
            "kb_id": kb_id,
            "file_id": file_id,
            "source_path": source_path,
            "source_updated_at": source_updated_at,
            "chunks_added": 1,
        }

