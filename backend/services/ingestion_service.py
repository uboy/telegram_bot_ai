"""
Сервис загрузки документов/веб-страниц/вики в базы знаний.

На первом этапе использует существующую синхронную логику rag_system и document_loader_manager,
перенесённую из кода Telegram-бота.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List
import json
import hashlib
import os
import re
import tempfile
import zipfile

from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from shared.database import KnowledgeImportLog, KnowledgeBase, KnowledgeChunk, Document, DocumentVersion, get_session  # type: ignore
from shared.rag_system import rag_system  # type: ignore
from shared.document_loaders import document_loader_manager  # type: ignore
from shared.rag_pipeline.classifier import classify_document  # type: ignore
from shared.utils import detect_language  # type: ignore
from shared.kb_settings import normalize_kb_settings, get_chunking_settings  # type: ignore
from shared.wiki_scraper import crawl_wiki_to_kb  # type: ignore
from shared.wiki_git_loader import load_wiki_from_git, load_wiki_from_zip  # type: ignore
from shared.image_processor import image_processor  # type: ignore
from shared.index_outbox_service import index_outbox_service  # type: ignore
from shared.logging_config import logger  # type: ignore

_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+", flags=re.UNICODE)
_SECTION_SEPARATOR_RE = re.compile(r"\s*>\s*")
_CREDENTIAL_URL_RE = re.compile(r"([a-z][a-z0-9+\-.]*://)([^/@:\s]+):([^/@\s]+)@", flags=re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(?i)\b(authorization\b\s*:\s*(?:bearer|basic|token)\s+)([^\s,;]+)")
_BEARER_TOKEN_RE = re.compile(r"(?i)\b(bearer\s+)([^\s,;]+)")
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|secret)\b(\s*[:=]\s*)([^\s,;]+)"
)


class IngestionService:
    def __init__(self, db: Session) -> None:
        self.db = db  # Используется только для совместимости, но не для логов во время массового импорта

    def _get_kb_settings(self, kb_id: int) -> Dict[str, Any]:
        kb = self.db.query(KnowledgeBase).filter_by(id=kb_id).first()
        return normalize_kb_settings(getattr(kb, "settings", None))

    def _build_loader_options(self, settings: Dict[str, Any], source_kind: str) -> Dict[str, Any]:
        chunking = get_chunking_settings(settings, source_kind) or {}
        mode = chunking.get("mode")
        if mode == "file":
            mode = "full"
        return {
            "chunking_mode": mode,
            "max_chars": chunking.get("max_chars"),
            "overlap": chunking.get("overlap"),
        }

    def _is_code_ext(self, ext: str) -> bool:
        code_exts = {".py", ".cpp", ".c", ".hpp", ".h", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".cs", ".rb", ".php", ".kt", ".swift"}
        return ext.lower() in code_exts

    def _code_lang_from_ext(self, ext: str) -> str:
        mapping = {
            ".py": "python",
            ".cpp": "cpp",
            ".c": "c",
            ".hpp": "cpp",
            ".h": "c",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".cs": "csharp",
            ".rb": "ruby",
            ".php": "php",
            ".kt": "kotlin",
            ".swift": "swift",
        }
        return mapping.get(ext.lower(), "")

    def _read_text_file(self, path: str) -> str:
        encodings = ["utf-8", "utf-8-sig", "cp1251", "latin-1", "windows-1251"]
        for encoding in encodings:
            try:
                with open(path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(path, "rb") as f:
            raw = f.read()
        for encoding in encodings:
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return ""

    def _get_existing_doc_hash(self, kb_id: int, source_path: str, source_type: str) -> str | None:
        doc = (
            self.db.query(Document)
            .filter_by(
                knowledge_base_id=kb_id,
                source_type=source_type,
                source_path=source_path,
            )
            .first()
        )
        if doc and getattr(doc, "content_hash", None):
            return doc.content_hash
        existing = (
            self.db.query(KnowledgeChunk)
            .filter_by(
                knowledge_base_id=kb_id,
                source_type=source_type,
                source_path=source_path,
            )
            .first()
        )
        if not existing or not existing.chunk_metadata:
            return None
        try:
            meta = json.loads(existing.chunk_metadata)
        except Exception:
            return None
        return meta.get("doc_hash")

    def _classify_from_chunks(self, chunks: List[Dict[str, str]], source_path: str | None) -> str:
        for chunk in chunks:
            content = (chunk.get("content") or "").strip()
            if content:
                return classify_document(content[:2000], source_path)
        return classify_document("", source_path)

    def _infer_language_from_chunks(self, chunks: List[Dict[str, str]]) -> str:
        for chunk in chunks:
            content = (chunk.get("content") or "").strip()
            if content:
                return detect_language(content) or "ru"
        return "ru"

    def _normalize_chunk_metadata(
        self,
        *,
        base_meta: Dict[str, Any],
        content: str,
        source_type: str,
        source_path: str,
        chunk_title: str,
        doc_class: str,
        language: str,
        doc_hash: str | None,
        doc_version: int,
        source_updated_at: str,
        chunk_no: int,
    ) -> Dict[str, Any]:
        meta: Dict[str, Any] = dict(base_meta or {})

        title = str(meta.get("title") or chunk_title or source_path or "").strip()
        doc_title = str(meta.get("doc_title") or title or source_path or "").strip()
        section_title = str(meta.get("section_title") or title or doc_title or "").strip()
        section_path = str(meta.get("section_path") or doc_title or source_path or "ROOT").strip()
        section_path_norm = self._normalize_section_path_norm(
            str(meta.get("section_path_norm") or section_path or "ROOT")
        )
        chunk_kind = str(meta.get("chunk_kind") or "text").strip() or "text"
        block_type = str(meta.get("block_type") or meta.get("chunk_type") or chunk_kind or "text").strip() or "text"
        page_no = self._coerce_optional_int(meta.get("page_no", meta.get("page")))
        char_start = self._coerce_optional_int(meta.get("char_start"))
        char_end = self._coerce_optional_int(meta.get("char_end"))
        parser_confidence = self._coerce_optional_float(meta.get("parser_confidence"))
        parser_warning = self._sanitize_parser_warning(meta.get("parser_warning"))
        parent_chunk_id = self._coerce_optional_ref(meta.get("parent_chunk_id"))
        prev_chunk_id = self._coerce_optional_ref(meta.get("prev_chunk_id"))
        next_chunk_id = self._coerce_optional_ref(meta.get("next_chunk_id"))
        token_count_est = self._estimate_token_count(content)
        parser_profile = str(meta.get("parser_profile") or f"loader:{source_type}:v1").strip() or f"loader:{source_type}:v1"
        stable_chunk_no = self._coerce_optional_int(meta.get("chunk_no")) or chunk_no
        chunk_hash = str(meta.get("chunk_hash") or self._build_chunk_hash(
            source_type=source_type,
            source_path=source_path,
            chunk_no=stable_chunk_no,
            content=content,
        )).strip()

        meta["type"] = str(meta.get("type") or source_type or "unknown")
        meta["title"] = title
        meta["doc_title"] = doc_title
        meta["section_title"] = section_title
        meta["section_path"] = section_path
        meta["section_path_norm"] = section_path_norm
        meta["chunk_kind"] = chunk_kind
        meta["block_type"] = block_type
        meta["chunk_no"] = stable_chunk_no
        meta["chunk_hash"] = chunk_hash
        meta["token_count_est"] = token_count_est
        meta["parser_profile"] = parser_profile

        meta["document_class"] = doc_class
        meta["language"] = language or "ru"
        if doc_hash:
            meta["doc_hash"] = doc_hash
        meta["doc_version"] = doc_version
        meta["source_updated_at"] = source_updated_at
        if page_no is not None:
            meta["page_no"] = page_no
        if char_start is not None:
            meta["char_start"] = char_start
        if char_end is not None:
            meta["char_end"] = char_end
        if parser_confidence is not None:
            meta["parser_confidence"] = parser_confidence
        if parser_warning:
            meta["parser_warning"] = parser_warning
        if parent_chunk_id:
            meta["parent_chunk_id"] = parent_chunk_id
        if prev_chunk_id:
            meta["prev_chunk_id"] = prev_chunk_id
        if next_chunk_id:
            meta["next_chunk_id"] = next_chunk_id
        return meta

    def _normalize_section_path_norm(self, section_path: str) -> str:
        normalized = str(section_path or "").replace("\\", "/").strip()
        normalized = _SECTION_SEPARATOR_RE.sub(" > ", normalized)
        normalized = _SPACE_RE.sub(" ", normalized).strip().lower()
        return normalized or "root"

    def _estimate_token_count(self, content: str) -> int:
        return len(_WORD_RE.findall(content or ""))

    def _build_chunk_hash(
        self,
        *,
        source_type: str,
        source_path: str,
        chunk_no: int,
        content: str,
    ) -> str:
        normalized_content = _SPACE_RE.sub(" ", content or "").strip()
        hash_basis = "\n".join(
            [
                str(source_type or "").strip(),
                str(source_path or "").strip(),
                str(chunk_no),
                normalized_content,
            ]
        )
        return hashlib.sha256(hash_basis.encode("utf-8", errors="ignore")).hexdigest()

    def _coerce_optional_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return None
        return coerced if coerced >= 0 else None

    def _coerce_optional_float(self, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, coerced))

    def _coerce_optional_ref(self, value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    def _sanitize_parser_warning(self, value: Any) -> str | None:
        if value is None:
            return None
        cleaned = _SPACE_RE.sub(" ", str(value)).strip()
        if not cleaned:
            return None
        cleaned = _CREDENTIAL_URL_RE.sub(r"\1***:***@", cleaned)
        cleaned = _AUTH_HEADER_RE.sub(r"\1***", cleaned)
        cleaned = _BEARER_TOKEN_RE.sub(r"\1***", cleaned)
        cleaned = _SECRET_VALUE_RE.sub(r"\1\2***", cleaned)
        return cleaned[:500]

    def _chunk_columns_from_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "chunk_hash": meta.get("chunk_hash"),
            "chunk_no": meta.get("chunk_no"),
            "block_type": meta.get("block_type"),
            "parent_chunk_id": meta.get("parent_chunk_id"),
            "prev_chunk_id": meta.get("prev_chunk_id"),
            "next_chunk_id": meta.get("next_chunk_id"),
            "section_path_norm": meta.get("section_path_norm"),
            "page_no": meta.get("page_no"),
            "char_start": meta.get("char_start"),
            "char_end": meta.get("char_end"),
            "token_count_est": meta.get("token_count_est"),
            "parser_profile": meta.get("parser_profile"),
            "parser_confidence": meta.get("parser_confidence"),
            "parser_warning": meta.get("parser_warning"),
        }

    def _build_canonical_chunk_payload(
        self,
        *,
        base_meta: Dict[str, Any],
        content: str,
        source_type: str,
        source_path: str,
        chunk_title: str,
        doc_class: str,
        language: str,
        doc_hash: str | None,
        doc_version: int,
        source_updated_at: str,
        chunk_no: int,
    ) -> Dict[str, Any]:
        canonical_meta = self._normalize_chunk_metadata(
            base_meta=base_meta,
            content=content,
            source_type=source_type,
            source_path=source_path,
            chunk_title=chunk_title,
            doc_class=doc_class,
            language=language,
            doc_hash=doc_hash,
            doc_version=doc_version,
            source_updated_at=source_updated_at,
            chunk_no=chunk_no,
        )
        return {
            "metadata": canonical_meta,
            "metadata_json": dict(canonical_meta),
            "chunk_columns": self._chunk_columns_from_metadata(canonical_meta),
        }

    def _upsert_document(
        self,
        kb_id: int,
        source_type: str,
        source_path: str,
        doc_hash: str,
        doc_class: str,
        language: str,
    ) -> tuple[int, int]:
        doc = (
            self.db.query(Document)
            .filter_by(
                knowledge_base_id=kb_id,
                source_type=source_type,
                source_path=source_path,
            )
            .first()
        )
        if not doc:
            doc = Document(
                knowledge_base_id=kb_id,
                source_type=source_type,
                source_path=source_path,
                content_hash=doc_hash,
                document_class=doc_class,
                language=language,
                current_version=1,
            )
            self.db.add(doc)
            self.db.flush()
            version = 1
            self.db.add(DocumentVersion(document_id=doc.id, version=version, content_hash=doc_hash))
            self.db.commit()
            return doc.id, version

        if doc.content_hash != doc_hash:
            doc.current_version = (doc.current_version or 0) + 1
            doc.content_hash = doc_hash
            doc.document_class = doc_class
            doc.language = language
            version = doc.current_version
            self.db.add(DocumentVersion(document_id=doc.id, version=version, content_hash=doc_hash))
            self.db.commit()
            return doc.id, version

        return doc.id, doc.current_version or 1
    
    def _write_import_log(self, log_obj: KnowledgeImportLog) -> None:
        """Записать лог импорта через отдельную короткую сессию"""
        with get_session() as session:
            session.add(log_obj)
            # commit выполнится автоматически при выходе из with

    def _build_wiki_ingest_result(
        self,
        *,
        wiki_root: str,
        deleted_chunks: int,
        pages_processed: int | None,
        files_processed: int | None,
        chunks_added: int,
        crawl_mode: str,
        git_fallback_attempted: bool,
        status: str | None = None,
        stage: str | None = None,
        failure_reason: str | None = None,
        failure_message: str | None = None,
        recovery_options: List[str] | None = None,
    ) -> Dict[str, Any]:
        return {
            "deleted_chunks": deleted_chunks,
            "pages_processed": pages_processed,
            "files_processed": files_processed,
            "chunks_added": chunks_added,
            "wiki_root": wiki_root,
            "crawl_mode": crawl_mode,
            "git_fallback_attempted": git_fallback_attempted,
            "status": status or "success",
            "stage": stage or crawl_mode,
            "failure_reason": failure_reason,
            "failure_message": failure_message,
            "recovery_options": list(recovery_options or []),
        }

    def _enqueue_index_upsert_event(
        self,
        *,
        kb_id: int,
        document_id: int | None,
        version: int | None,
        source_type: str,
        source_path: str,
        chunks_added: int,
    ) -> None:
        if chunks_added <= 0:
            return
        payload = {
            "source_type": source_type,
            "source_path": source_path,
            "chunks_added": int(chunks_added),
            "version": int(version) if version is not None else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            index_outbox_service.enqueue_event(
                operation="UPSERT",
                knowledge_base_id=int(kb_id),
                document_id=int(document_id) if document_id is not None else None,
                version=int(version) if version is not None else None,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Outbox enqueue failed: kb_id=%s document_id=%s source=%s error=%s",
                kb_id,
                document_id,
                source_path,
                exc,
            )

    def ingest_web_page(self, kb_id: int, url: str, telegram_id: str | None, username: str | None) -> Dict[str, Any]:
        """Загрузить одну веб-страницу в базу знаний."""
        settings = self._get_kb_settings(kb_id)
        options = self._build_loader_options(settings, "web")
        chunks = document_loader_manager.load_document(url, "web", options=options)
        doc_class = self._classify_from_chunks(chunks, url)
        doc_language = self._infer_language_from_chunks(chunks)
        combined = "".join([(chunk.get("content") or "") for chunk in chunks])
        doc_hash = hashlib.sha256(combined.encode("utf-8", errors="ignore")).hexdigest()
        document_id, doc_version = self._upsert_document(
            kb_id=kb_id,
            source_type="web",
            source_path=url,
            doc_hash=doc_hash,
            doc_class=doc_class,
            language=doc_language,
        )

        # Удалить старые фрагменты этой страницы (обновление версии)
        rag_system.delete_chunks_by_source_exact(
            knowledge_base_id=kb_id,
            source_type="web",
            source_path=url,
        )

        source_updated_at = datetime.now(timezone.utc).isoformat()

        # Использовать batch insert для лучшей производительности
        chunks_data = []
        for chunk_no, chunk in enumerate(chunks, start=1):
            content = chunk.get("content", "")
            chunk_payload = self._build_canonical_chunk_payload(
                base_meta=dict(chunk.get("metadata") or {}),
                content=content,
                source_type="web",
                source_path=url,
                chunk_title=str(chunk.get("title") or url),
                doc_class=doc_class,
                language=(detect_language(content) if content else doc_language),
                doc_hash=None,
                doc_version=doc_version,
                source_updated_at=source_updated_at,
                chunk_no=chunk_no,
            )
            chunks_data.append({
                'knowledge_base_id': kb_id,
                'content': content,
                'document_id': document_id,
                'version': doc_version,
                'source_type': "web",
                'source_path': url,
                'metadata': chunk_payload["metadata"],
                'metadata_json': chunk_payload["metadata_json"],
                'chunk_columns': chunk_payload["chunk_columns"],
            })
        
        # Добавить все чанки пакетно
        if chunks_data:
            import time
            max_retries = 3
            base_delay = 0.1
            added = 0
            
            for attempt in range(max_retries):
                try:
                    rag_system.add_chunks_batch(chunks_data)
                    added = len(chunks_data)
                    break
                except OperationalError as e:
                    if "database is locked" in str(e).lower():
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"Database locked, retrying batch insert in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"Failed to add chunks after {max_retries} retries: {e}")
                            raise
                    else:
                        raise
                except Exception as e:
                    # Для других ошибок не делаем fallback на поштучную вставку
                    logger.error(f"Batch insert failed: {e}")
                    raise
        else:
            added = 0

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
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=document_id,
            version=doc_version,
            source_type="web",
            source_path=url,
            chunks_added=added,
        )

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
        settings = self._get_kb_settings(kb_id)
        options = self._build_loader_options(settings, "wiki")
        stats = crawl_wiki_to_kb(
            base_url=wiki_url,
            knowledge_base_id=kb_id,
            max_pages=500,
            loader_options=options,
        )
        deleted = stats.get("deleted_chunks", 0)
        pages = stats.get("pages_processed", 0)
        added = stats.get("chunks_added", 0)
        wiki_root = stats.get("wiki_root", wiki_url)
        crawl_mode = str(stats.get("crawl_mode", "html") or "html")
        git_fallback_attempted = bool(stats.get("git_fallback_attempted", False))

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
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=None,
            version=None,
            source_type="wiki",
            source_path=wiki_root,
            chunks_added=added,
        )

        return self._build_wiki_ingest_result(
            wiki_root=wiki_root,
            deleted_chunks=deleted,
            pages_processed=pages,
            files_processed=None,
            chunks_added=added,
            crawl_mode=crawl_mode,
            git_fallback_attempted=git_fallback_attempted,
            status=str(stats.get("status") or "success"),
            stage=str(stats.get("stage") or crawl_mode),
            failure_reason=stats.get("failure_reason"),
            failure_message=stats.get("failure_message"),
            recovery_options=list(stats.get("recovery_options") or []),
        )

    def ingest_wiki_git(
        self,
        kb_id: int,
        wiki_url: str,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        """Загрузить вики из Git-репозитория (через wiki_git_loader)."""
        settings = self._get_kb_settings(kb_id)
        options = self._build_loader_options(settings, "wiki")
        stats = load_wiki_from_git(wiki_url, kb_id, loader_options=options)
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
        self._write_import_log(log)
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=None,
            version=None,
            source_type="wiki_git",
            source_path=wiki_root,
            chunks_added=added,
        )

        return self._build_wiki_ingest_result(
            wiki_root=wiki_root,
            deleted_chunks=deleted,
            pages_processed=None,
            files_processed=files,
            chunks_added=added,
            crawl_mode="git",
            git_fallback_attempted=False,
        )

    def ingest_wiki_zip(
        self,
        kb_id: int,
        wiki_url: str,
        zip_path: str,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        """Загрузить вики из ZIP архива (через wiki_git_loader.load_wiki_from_zip)."""
        settings = self._get_kb_settings(kb_id)
        options = self._build_loader_options(settings, "wiki")
        stats = load_wiki_from_zip(zip_path, wiki_url, kb_id, loader_options=options)
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
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=None,
            version=None,
            source_type="wiki_zip",
            source_path=wiki_root,
            chunks_added=added,
        )

        result = self._build_wiki_ingest_result(
            wiki_root=wiki_root,
            deleted_chunks=deleted,
            pages_processed=None,
            files_processed=files,
            chunks_added=added,
            crawl_mode="zip",
            git_fallback_attempted=False,
        )
        result["processed_files"] = processed_files
        return result

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
        file_type = (file_type or "").strip().lower()
        if not file_type and file_name:
            inferred_ext = os.path.splitext(file_name)[1].lstrip(".").lower()
            if inferred_ext == "markdown":
                inferred_ext = "md"
            file_type = inferred_ext
        settings = self._get_kb_settings(kb_id)

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
                    inner_ext = os.path.splitext(name)[1].lower()
                    inner_ext_clean = inner_ext.lstrip(".").lower()
                    # Извлечь во временный файл
                    with zf.open(name) as src, tempfile.NamedTemporaryFile(delete=False, suffix=inner_ext) as dst:
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
                        source_type=inner_ext_clean or "unknown",
                        source_path=source_path,
                    )
                    try:
                        source_kind = "markdown" if inner_ext_clean in ("md", "markdown") else "text"
                        if self._is_code_ext(inner_ext):
                            source_kind = "code"
                        options = self._build_loader_options(settings, source_kind)
                        chunks = document_loader_manager.load_document(inner_path, inner_ext_clean or None, options=options)
                        # Фильтруем пустые чанки (менее 10 символов)
                        chunks = [
                            chunk
                            for chunk in chunks
                            if chunk.get("content", "").strip()
                            and len(chunk.get("content", "").strip()) > 10
                        ]
                    except Exception:  # noqa: BLE001
                        chunks = []
                    doc_class = self._classify_from_chunks(chunks, source_path)
                    added = 0
                    doc_language = self._infer_language_from_chunks(chunks)
                    document_id, doc_version = self._upsert_document(
                        kb_id=kb_id,
                        source_type=inner_ext_clean or "unknown",
                        source_path=source_path,
                        doc_hash=doc_hash,
                        doc_class=doc_class,
                        language=doc_language,
                    )
                    source_updated_at = datetime.now(timezone.utc).isoformat()

                    # Использовать batch insert для лучшей производительности
                    chunks_data = []
                    for chunk_no, chunk in enumerate(chunks, start=1):
                        content = chunk.get("content", "")
                        chunk_payload = self._build_canonical_chunk_payload(
                            base_meta=dict(chunk.get("metadata") or {}),
                            content=content,
                            source_type=inner_ext_clean or "unknown",
                            source_path=source_path,
                            chunk_title=str(chunk.get("title") or name),
                            doc_class=doc_class,
                            language=(detect_language(content) if content else doc_language),
                            doc_hash=doc_hash,
                            doc_version=doc_version,
                            source_updated_at=source_updated_at,
                            chunk_no=chunk_no,
                        )
                        base_meta = chunk_payload["metadata"]
                        if inner_ext_clean in ("md", "markdown"):
                            original_title = base_meta.get("doc_title") or base_meta.get("title") or ""
                            base_meta["doc_title"] = source_path
                            if not base_meta.get("section_path") or base_meta.get("section_path") == original_title:
                                base_meta["section_path"] = source_path
                            base_meta["section_path_norm"] = self._normalize_section_path_norm(source_path)
                            if base_meta.get("title") == original_title or not base_meta.get("title"):
                                base_meta["title"] = source_path
                        if self._is_code_ext(inner_ext):
                            base_meta["chunk_kind"] = "code_file"
                            base_meta["block_type"] = "code_file"
                            base_meta["code_lang"] = self._code_lang_from_ext(inner_ext)

                        chunks_data.append({
                            'knowledge_base_id': kb_id,
                            'content': content,
                            'document_id': document_id,
                            'version': doc_version,
                            'source_type': inner_ext_clean or "unknown",
                            'source_path': source_path,
                            'metadata': base_meta,
                            'metadata_json': dict(base_meta),
                            'chunk_columns': self._chunk_columns_from_metadata(base_meta),
                        })
                    
                    # Добавить все чанки пакетно
                    if chunks_data:
                        import time
                        max_retries = 3
                        base_delay = 0.1
                        added = 0
                        
                        for attempt in range(max_retries):
                            try:
                                rag_system.add_chunks_batch(chunks_data)
                                added = len(chunks_data)
                                break
                            except OperationalError as e:
                                if "database is locked" in str(e).lower():
                                    if attempt < max_retries - 1:
                                        delay = base_delay * (2 ** attempt)
                                        logger.warning(f"Database locked for {name}, retrying batch insert in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                                        time.sleep(delay)
                                        continue
                                    else:
                                        logger.error(f"Failed to add chunks from {name} after {max_retries} retries: {e}")
                                        raise
                                else:
                                    raise
                            except Exception as e:
                                # Для других ошибок не делаем fallback на поштучную вставку
                                logger.error(f"Batch insert failed for {name}: {e}")
                                raise
                    else:
                        added = 0
                    total_chunks += added
                    per_file_stats.append(
                        {
                            "name": name,
                            "chunks_added": added,
                        }
                    )
                    # Записать в журнал загрузок для каждого файла через отдельную короткую сессию
                    log = KnowledgeImportLog(
                        knowledge_base_id=kb_id,
                        user_telegram_id=tg_id,
                        username=username_val,
                        action_type="archive",
                        source_path=source_path,
                        total_chunks=added,
                    )
                    self._write_import_log(log)
                    self._enqueue_index_upsert_event(
                        kb_id=kb_id,
                        document_id=document_id,
                        version=doc_version,
                        source_type=inner_ext_clean or "unknown",
                        source_path=source_path,
                        chunks_added=added,
                    )
                    try:
                        os.remove(inner_path)
                    except OSError:
                        pass
                # Финальный commit для оставшихся логов
                self.db.commit()

            return {
                "mode": "archive",
                "kb_id": kb_id,
                "file_name": file_name,
                "total_chunks": total_chunks,
                "files": per_file_stats,
            }

        # Поддержка чата по расширению
        if file_type in ("chat", "json", "txtchat"):
            chunks = document_loader_manager.load_document(file_path, "chat", options={})
            doc_class = self._classify_from_chunks(chunks, file_name)
            doc_language = self._infer_language_from_chunks(chunks)
            with open(file_path, "rb") as f:
                data = f.read()
            doc_hash = hashlib.sha256(data).hexdigest()
            source_path = file_name or ""

            rag_system.delete_chunks_by_source_exact(
                knowledge_base_id=kb_id,
                source_type="chat",
                source_path=source_path,
            )

            document_id, doc_version = self._upsert_document(
                kb_id=kb_id,
                source_type="chat",
                source_path=source_path,
                doc_hash=doc_hash,
                doc_class=doc_class,
                language=doc_language,
            )
            source_updated_at = datetime.now(timezone.utc).isoformat()
            chunks_data = []
            for chunk_no, chunk in enumerate(chunks, start=1):
                content = chunk.get("content", "")
                chunk_payload = self._build_canonical_chunk_payload(
                    base_meta=dict(chunk.get("metadata") or {}),
                    content=content,
                    source_type="chat",
                    source_path=source_path,
                    chunk_title=str(chunk.get("title") or source_path),
                    doc_class=doc_class,
                    language=(detect_language(content) if content else doc_language),
                    doc_hash=doc_hash,
                    doc_version=doc_version,
                    source_updated_at=source_updated_at,
                    chunk_no=chunk_no,
                )
                chunks_data.append({
                    "knowledge_base_id": kb_id,
                    "content": content,
                    "document_id": document_id,
                    "version": doc_version,
                    "source_type": "chat",
                    "source_path": source_path,
                    "metadata": chunk_payload["metadata"],
                    "metadata_json": chunk_payload["metadata_json"],
                    "chunk_columns": chunk_payload["chunk_columns"],
                })
            if chunks_data:
                rag_system.add_chunks_batch(chunks_data)
                added = len(chunks_data)
            else:
                added = 0
            log = KnowledgeImportLog(
                knowledge_base_id=kb_id,
                user_telegram_id=telegram_id or "",
                username=username or telegram_id or "",
                action_type="chat",
                source_path=source_path,
                total_chunks=added,
            )
            self.db.add(log)
            self.db.commit()
            self._enqueue_index_upsert_event(
                kb_id=kb_id,
                document_id=document_id,
                version=doc_version,
                source_type="chat",
                source_path=source_path,
                chunks_added=added,
            )
            return {
                "mode": "chat",
                "kb_id": kb_id,
                "file_name": file_name,
                "total_chunks": added,
                "doc_version": doc_version,
                "source_updated_at": source_updated_at,
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

        source_kind = "markdown" if file_type in ("md", "markdown") else "text"
        ext = f".{file_type}" if file_type else ""
        if self._is_code_ext(ext):
            source_kind = "code"
        options = self._build_loader_options(settings, source_kind)
        chunks = document_loader_manager.load_document(file_path, file_type or None, options=options)
        doc_class = self._classify_from_chunks(chunks, source_path)
        doc_language = self._infer_language_from_chunks(chunks)
        document_id, doc_version = self._upsert_document(
            kb_id=kb_id,
            source_type=file_type or "unknown",
            source_path=source_path,
            doc_hash=doc_hash,
            doc_class=doc_class,
            language=doc_language,
        )

        source_updated_at = datetime.now(timezone.utc).isoformat()

        # Использовать batch insert для лучшей производительности
        chunks_data = []
        for chunk_no, chunk in enumerate(chunks, start=1):
            content = chunk.get("content", "")
            chunk_payload = self._build_canonical_chunk_payload(
                base_meta=dict(chunk.get("metadata") or {}),
                content=content,
                source_type=file_type or "unknown",
                source_path=source_path,
                chunk_title=str(chunk.get("title") or source_path),
                doc_class=doc_class,
                language=(detect_language(content) if content else doc_language),
                doc_hash=doc_hash,
                doc_version=doc_version,
                source_updated_at=source_updated_at,
                chunk_no=chunk_no,
            )
            base_meta = chunk_payload["metadata"]
            if file_type in ("md", "markdown"):
                original_title = base_meta.get("doc_title") or base_meta.get("title") or ""
                base_meta["doc_title"] = source_path
                if not base_meta.get("section_path") or base_meta.get("section_path") == original_title:
                    base_meta["section_path"] = source_path
                base_meta["section_path_norm"] = self._normalize_section_path_norm(source_path)
                if base_meta.get("title") == original_title or not base_meta.get("title"):
                    base_meta["title"] = source_path
            if self._is_code_ext(ext):
                base_meta["chunk_kind"] = "code_file"
                base_meta["block_type"] = "code_file"
                base_meta["code_lang"] = self._code_lang_from_ext(ext)

            chunks_data.append({
                'knowledge_base_id': kb_id,
                'content': content,
                'document_id': document_id,
                'version': doc_version,
                'source_type': file_type or "unknown",
                'source_path': source_path,
                'metadata': base_meta,
                'metadata_json': dict(base_meta),
                'chunk_columns': self._chunk_columns_from_metadata(base_meta),
            })
        
        # Добавить все чанки пакетно
        if chunks_data:
            import time
            max_retries = 3
            base_delay = 0.1
            added = 0
            
            for attempt in range(max_retries):
                try:
                    rag_system.add_chunks_batch(chunks_data)
                    added = len(chunks_data)
                    break
                except OperationalError as e:
                    if "database is locked" in str(e).lower():
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"Database locked, retrying batch insert in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"Failed to add chunks after {max_retries} retries: {e}")
                            raise
                    else:
                        raise
                except Exception as e:
                    # Для других ошибок не делаем fallback на поштучную вставку
                    logger.error(f"Batch insert failed: {e}")
                    raise
        else:
            added = 0

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
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=document_id,
            version=doc_version,
            source_type=file_type or "unknown",
            source_path=source_path,
            chunks_added=added,
        )

        return {
            "mode": "document",
            "kb_id": kb_id,
            "file_name": file_name,
            "total_chunks": added,
            "doc_version": doc_version,
            "source_updated_at": source_updated_at,
        }

    # === Codebase ingestion ===

    def ingest_codebase_path(
        self,
        kb_id: int,
        code_path: str,
        telegram_id: str | None,
        username: str | None,
        repo_label: str | None = None,
    ) -> Dict[str, Any]:
        settings = self._get_kb_settings(kb_id)
        options = self._build_loader_options(settings, "code")

        root_abs = os.path.abspath(code_path)
        if not os.path.isdir(root_abs):
            raise ValueError(f"Каталог не найден: {code_path}")

        allowed_root = os.getenv("CODEBASE_ROOT", "").strip()
        if allowed_root:
            allowed_abs = os.path.abspath(allowed_root)
            if os.path.commonpath([root_abs, allowed_abs]) != allowed_abs:
                raise ValueError("Путь вне разрешенного CODEBASE_ROOT")

        repo_prefix = repo_label or root_abs
        exclude_dirs = {
            ".git",
            ".idea",
            ".venv",
            "node_modules",
            "dist",
            "build",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
        }

        files_processed = 0
        files_skipped = 0
        files_updated = 0
        chunks_added = 0
        source_updated_at = datetime.now(timezone.utc).isoformat()

        for dirpath, dirnames, filenames in os.walk(root_abs):
            dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
            for filename in filenames:
                ext = os.path.splitext(filename)[1]
                if not self._is_code_ext(ext):
                    continue
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, root_abs).replace(os.sep, "/")
                source_path = f"{repo_prefix}::{rel_path}"

                try:
                    with open(full_path, "rb") as f:
                        data = f.read()
                except OSError:
                    continue

                doc_hash = hashlib.sha256(data).hexdigest()
                existing_hash = self._get_existing_doc_hash(kb_id, source_path, "code")
                if existing_hash == doc_hash:
                    files_skipped += 1
                    continue

                rag_system.delete_chunks_by_source_exact(
                    knowledge_base_id=kb_id,
                    source_type="code",
                    source_path=source_path,
                )

                chunks = document_loader_manager.load_document(
                    full_path,
                    ext.lstrip(".") or None,
                    options=options,
                )
                chunks = [
                    chunk
                    for chunk in chunks
                    if chunk.get("content", "").strip()
                    and len(chunk.get("content", "").strip()) > 10
                ]
                doc_class = self._classify_from_chunks(chunks, source_path)
                doc_language = self._infer_language_from_chunks(chunks)
                document_id, doc_version = self._upsert_document(
                    kb_id=kb_id,
                    source_type="code",
                    source_path=source_path,
                    doc_hash=doc_hash,
                    doc_class=doc_class,
                    language=doc_language,
                )

                chunks_data = []
                for chunk_no, chunk in enumerate(chunks, start=1):
                    content = chunk.get("content", "")
                    chunk_payload = self._build_canonical_chunk_payload(
                        base_meta=dict(chunk.get("metadata") or {}),
                        content=content,
                        source_type="code",
                        source_path=source_path,
                        chunk_title=rel_path,
                        doc_class=doc_class,
                        language=(detect_language(content) if content else doc_language),
                        doc_hash=doc_hash,
                        doc_version=doc_version,
                        source_updated_at=source_updated_at,
                        chunk_no=chunk_no,
                    )
                    base_meta = chunk_payload["metadata"]
                    base_meta["chunk_kind"] = "code_file"
                    base_meta["block_type"] = "code_file"
                    base_meta["code_lang"] = self._code_lang_from_ext(ext)
                    base_meta["file_path"] = rel_path
                    base_meta["repo_root"] = repo_prefix

                    chunks_data.append({
                        "knowledge_base_id": kb_id,
                        "content": content,
                        "document_id": document_id,
                        "version": doc_version,
                        "source_type": "code",
                        "source_path": source_path,
                        "metadata": base_meta,
                        "metadata_json": dict(base_meta),
                        "chunk_columns": self._chunk_columns_from_metadata(base_meta),
                    })

                if chunks_data:
                    rag_system.add_chunks_batch(chunks_data)
                    chunks_added += len(chunks_data)
                    files_updated += 1
                    self._enqueue_index_upsert_event(
                        kb_id=kb_id,
                        document_id=document_id,
                        version=doc_version,
                        source_type="code",
                        source_path=source_path,
                        chunks_added=len(chunks_data),
                    )
                files_processed += 1

        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=telegram_id or "",
            username=username or telegram_id or "",
            action_type="codebase",
            source_path=repo_prefix,
            total_chunks=chunks_added,
        )
        self.db.add(log)
        self.db.commit()
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=None,
            version=None,
            source_type="codebase",
            source_path=repo_prefix,
            chunks_added=chunks_added,
        )

        return {
            "kb_id": kb_id,
            "root": repo_prefix,
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "files_updated": files_updated,
            "chunks_added": chunks_added,
        }

    def ingest_codebase_git(
        self,
        kb_id: int,
        git_url: str,
        telegram_id: str | None,
        username: str | None,
    ) -> Dict[str, Any]:
        import subprocess
        import shutil
        import tempfile
        from urllib.parse import urlparse

        temp_dir = tempfile.mkdtemp(prefix="code_git_")
        try:
            repo_path = os.path.join(temp_dir, "repo")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", git_url, repo_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or "git clone failed")

            parsed = urlparse(git_url)
            repo_label = os.path.basename(parsed.path).replace(".git", "") or git_url
            return self.ingest_codebase_path(
                kb_id=kb_id,
                code_path=repo_path,
                telegram_id=telegram_id,
                username=username,
                repo_label=repo_label,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
        doc_class = classify_document(processed_text[:2000], source_path)
        doc_language = detect_language(processed_text) if processed_text else "ru"
        doc_hash = hashlib.sha256((processed_text or "").encode("utf-8", errors="ignore")).hexdigest()
        document_id, doc_version = self._upsert_document(
            kb_id=kb_id,
            source_type="image",
            source_path=source_path,
            doc_hash=doc_hash,
            doc_class=doc_class,
            language=doc_language,
        )

        # Удалить старый фрагмент для этого изображения (если был)
        rag_system.delete_chunks_by_source_exact(
            knowledge_base_id=kb_id,
            source_type="image",
            source_path=source_path,
        )

        image_payload = self._build_canonical_chunk_payload(
            base_meta={"file_id": file_id},
            content=processed_text,
            source_type="image",
            source_path=source_path,
            chunk_title=source_path,
            doc_class=doc_class,
            language=doc_language,
            doc_hash=doc_hash,
            doc_version=doc_version,
            source_updated_at=source_updated_at,
            chunk_no=1,
        )

        rag_system.add_chunk(
            knowledge_base_id=kb_id,
            content=processed_text,
            source_type="image",
            source_path=source_path,
            metadata=image_payload["metadata"],
            metadata_json=image_payload["metadata_json"],
            chunk_columns=image_payload["chunk_columns"],
            document_id=document_id,
            version=doc_version,
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
        self._enqueue_index_upsert_event(
            kb_id=kb_id,
            document_id=document_id,
            version=doc_version,
            source_type="image",
            source_path=source_path,
            chunks_added=1,
        )

        return {
            "kb_id": kb_id,
            "file_id": file_id,
            "source_path": source_path,
            "source_updated_at": source_updated_at,
            "chunks_added": 1,
        }

