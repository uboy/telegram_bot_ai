"""
Функции для загрузки вики Gitee через git-репозиторий и восстановления ссылок на оригинальные страницы.
"""

import os
import re
import tempfile
import subprocess
import zipfile
import shutil
from typing import Dict, Optional
from urllib.parse import urlparse, unquote
from pathlib import Path

from shared.logging_config import logger
from shared.rag_system import rag_system
from shared.document_loaders import document_loader_manager
from shared.wiki_scraper import _normalize_base_url


def _normalize_wiki_rel_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip("/")


_TEMP_TITLE_RE = re.compile(r"^tmp[a-z0-9_-]{4,}$", flags=re.IGNORECASE)


def _wiki_page_path_from_file_path(file_path: str) -> str:
    normalized = _normalize_wiki_rel_path(file_path)
    if normalized.lower().endswith(".md"):
        normalized = normalized[:-3]
    return normalized


def _decorate_wiki_chunk_metadata(metadata: Dict[str, object], file_path: str) -> Dict[str, object]:
    normalized_path = _normalize_wiki_rel_path(file_path)
    wiki_page_path = _wiki_page_path_from_file_path(normalized_path)
    page_doc_title = Path(wiki_page_path).name or Path(normalized_path).stem or "wiki"

    enriched = dict(metadata or {})
    enriched["file_path"] = normalized_path
    enriched["wiki_page_path"] = wiki_page_path
    current_doc_title = str(enriched.get("doc_title") or "").strip()
    current_section_title = str(enriched.get("section_title") or "").strip()
    enriched["doc_title"] = page_doc_title if not current_doc_title or _TEMP_TITLE_RE.match(current_doc_title) else current_doc_title
    enriched["section_title"] = (
        page_doc_title
        if not current_section_title or _TEMP_TITLE_RE.match(current_section_title)
        else current_section_title
    )

    section_path = str(enriched.get("section_path") or "").strip()
    if section_path:
        if wiki_page_path and not section_path.startswith(wiki_page_path):
            enriched["section_path"] = f"{wiki_page_path} > {section_path}"
        else:
            enriched["section_path"] = section_path
    else:
        enriched["section_path"] = wiki_page_path or page_doc_title or "ROOT"
    return enriched


def _stable_chunk_title(raw_title: object, *, fallback: str) -> str:
    title = str(raw_title or "").strip()
    if not title or _TEMP_TITLE_RE.match(title):
        return str(fallback or "").strip() or "wiki"
    return title


def _extract_repo_info_from_wiki_url(wiki_url: str) -> Optional[Dict[str, str]]:
    """
    Извлечь информацию о репозитории из URL вики Gitee.

    Поддерживаемые форматы:
      https://gitee.com/mazurdenis/open-harmony/wikis         (web URL)
      https://gitee.com/mazurdenis/open-harmony.wiki.git      (git clone URL)
      https://gitee.com/mazurdenis/open-harmony.wikis.git     (git clone URL alt)
    -> {'owner': 'mazurdenis', 'repo': 'open-harmony', ...}
    """
    import re

    try:
        parsed = urlparse(wiki_url)
        path_parts = [p for p in parsed.path.split('/') if p]

        owner: Optional[str] = None
        repo: Optional[str] = None

        # Случай 1: git clone URL — last segment ends with .wiki.git / .wikis.git
        if len(path_parts) >= 2:
            last = path_parts[-1]
            m = re.match(r"^(.+?)\.wikis?\.git$", last, re.IGNORECASE)
            if m:
                owner = path_parts[-2]
                repo = m.group(1)

        # Случай 2: web URL — найти сегмент /wikis
        if owner is None:
            wiki_index = -1
            for i, part in enumerate(path_parts):
                if part == 'wikis':
                    wiki_index = i
                    break

            if wiki_index < 2:
                logger.warning(f"[wiki-git] Не удалось извлечь информацию о репозитории из URL: {wiki_url}")
                return None

            owner = path_parts[0]
            repo = path_parts[1]

        base_url = f"{parsed.scheme}://{parsed.netloc}/{owner}/{repo}"
        git_urls = _build_candidate_git_urls(parsed.scheme, parsed.netloc, owner, repo, base_url)
        git_url = git_urls[0]
        wiki_root = f"{base_url}/wikis"

        return {
            'owner': owner,
            'repo': repo,
            'base_url': base_url,
            'git_url': git_url,
            'git_urls': git_urls,
            'wiki_root': wiki_root,
        }
    except Exception as e:
        logger.error(f"[wiki-git] Ошибка при извлечении информации о репозитории: {e}")
        return None


def _build_candidate_git_urls(scheme: str, netloc: str, owner: str, repo: str, base_url: str) -> list[str]:
    candidates = [
        f"{scheme}://{netloc}/{owner}/{repo}.wiki.git",
        f"{scheme}://{netloc}/{owner}/{repo}.wikis.git",
        f"{base_url}/wikis.git",
        f"{base_url}.git",
    ]
    deduped: list[str] = []
    for url in candidates:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _build_non_interactive_git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env.setdefault("GIT_ASKPASS", "echo")
    env.setdefault("SSH_ASKPASS", "echo")
    return env


def _clone_wiki_repo(git_url: str, temp_dir: str, repo_dir_name: str = "wiki_repo") -> tuple[Optional[str], str]:
    """
    Клонировать git-репозиторий вики во временную директорию.

    Returns:
        (repo_path, error_detail) — repo_path=None при ошибке, error_detail содержит причину.
    """
    try:
        repo_path = os.path.join(temp_dir, repo_dir_name)

        logger.info(f"[wiki-git] Клонирование репозитория: {git_url}")
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', git_url, repo_path],
            capture_output=True,
            text=True,
            timeout=300,  # 5 минут на клонирование
            env=_build_non_interactive_git_env(),
        )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.error(f"[wiki-git] Ошибка клонирования {git_url}: {stderr}")
            return None, stderr or f"returncode={result.returncode}"

        logger.info(f"[wiki-git] Репозиторий успешно клонирован: {repo_path}")
        return repo_path, ""
    except subprocess.TimeoutExpired:
        msg = f"таймаут при клонировании {git_url}"
        logger.error(f"[wiki-git] {msg}")
        return None, msg
    except Exception as e:
        msg = f"исключение при клонировании {git_url}: {e}"
        logger.error(f"[wiki-git] {msg}")
        return None, msg


def _clone_first_available_wiki_repo(git_urls: list[str], temp_dir: str) -> Optional[str]:
    errors: list[str] = []
    for idx, git_url in enumerate(git_urls, start=1):
        repo_path, err = _clone_wiki_repo(git_url, temp_dir, repo_dir_name=f"wiki_repo_{idx}")
        if repo_path:
            return repo_path
        errors.append(f"{git_url}: {err}" if err else git_url)
    if errors:
        raise RuntimeError("Не удалось клонировать ни один из кандидатов:\n" + "\n".join(errors))
    return None


def _create_wiki_zip(repo_path: str, temp_dir: str) -> Optional[str]:
    """
    Создать ZIP архив из файлов вики в репозитории.
    
    Returns:
        Путь к созданному ZIP файлу или None при ошибке
    """
    try:
        zip_path = os.path.join(temp_dir, "wiki.zip")
        
        # Найти все markdown файлы в репозитории
        md_files = []
        for root, dirs, files in os.walk(repo_path):
            # Пропустить .git директорию
            if '.git' in dirs:
                dirs.remove('.git')
            
            for file in files:
                if file.endswith('.md'):
                    full_path = os.path.join(root, file)
                    # Относительный путь от корня репозитория
                    rel_path = os.path.relpath(full_path, repo_path)
                    md_files.append((full_path, rel_path))
        
        if not md_files:
            logger.warning(f"[wiki-git] Не найдено markdown файлов в репозитории")
            return None
        
        logger.info(f"[wiki-git] Найдено {len(md_files)} markdown файлов, создаю ZIP архив...")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for full_path, rel_path in md_files:
                zf.write(full_path, rel_path)
        
        logger.info(f"[wiki-git] ZIP архив создан: {zip_path} ({len(md_files)} файлов)")
        return zip_path
    except Exception as e:
        logger.error(f"[wiki-git] Ошибка при создании ZIP архива: {e}")
        return None


def _restore_wiki_url_from_path(file_path: str, wiki_root: str) -> str:
    """
    Восстановить URL страницы вики из пути к файлу в репозитории.
    
    Пример:
        file_path: "Sync&Build/Sync&Build.md"
        wiki_root: "https://gitee.com/mazurdenis/open-harmony/wikis"
        -> "https://gitee.com/mazurdenis/open-harmony/wikis/Sync&Build/Sync%26Build"
    
    Для Gitee вики структура обычно:
    - Файл: "Category/Page.md" -> URL: "wikis/Category/Page"
    - Файл: "Category/Category.md" -> URL: "wikis/Category"
    """
    try:
        # Убрать расширение .md
        original_path = _normalize_wiki_rel_path(file_path)
        if original_path.endswith('.md'):
            original_path = original_path[:-3]
        
        # Разделить путь на части
        path_parts = [p for p in original_path.split('/') if p]  # Убираем пустые части
        
        if not path_parts:
            logger.warning(f"[wiki-git] Пустой путь после обработки: {original_path}")
            return wiki_root
        
        # URL-кодировать каждую часть
        from urllib.parse import quote
        encoded_parts = []
        for part in path_parts:
            if part:
                # Кодируем специальные символы для URL
                # Gitee использует URL-кодирование для путей вики
                encoded = quote(part, safe='')  # Кодируем все специальные символы
                encoded_parts.append(encoded)
        
        # Собрать URL
        if encoded_parts:
            # Всегда используем полный путь с каталогами (подразделами вики)
            # Каталоги важны для структуры вики, поэтому сохраняем весь путь
            wiki_path = '/'.join(encoded_parts)
            
            result_url = f"{wiki_root}/{wiki_path}"
            logger.debug(f"[wiki-git] Восстановлен URL: {original_path} -> {result_url}")
            return result_url
        else:
            logger.warning(f"[wiki-git] Не удалось создать путь из частей: {path_parts}")
            return wiki_root
    except Exception as e:
        logger.error(f"[wiki-git] Ошибка при восстановлении URL из пути {file_path}: {e}", exc_info=True)
        return wiki_root


def load_wiki_from_git(
    wiki_url: str,
    knowledge_base_id: int,
    loader_options: dict | None = None,
) -> Dict[str, int]:
    """
    Загрузить вики Gitee через git-репозиторий и восстановить ссылки на оригинальные страницы.
    
    Args:
        wiki_url: URL вики (например, https://gitee.com/mazurdenis/open-harmony/wikis)
        knowledge_base_id: ID базы знаний
    
    Returns:
        Словарь со статистикой загрузки
    """
    repo_info = _extract_repo_info_from_wiki_url(wiki_url)
    if not repo_info:
        raise ValueError(f"Не удалось извлечь информацию о репозитории из URL: {wiki_url}")
    
    wiki_root = _normalize_base_url(wiki_url)
    
    # Удалить старые фрагменты этой вики
    logger.info(f"[wiki-git] Удаление старых фрагментов вики: {wiki_root}")
    deleted_chunks = rag_system.delete_chunks_by_source_prefix(
        knowledge_base_id=knowledge_base_id,
        source_type="web",
        source_prefix=wiki_root,
    )
    
    # Создать временную директорию
    temp_dir = tempfile.mkdtemp(prefix="wiki_git_")
    chunks_added = 0
    files_processed = 0
    
    try:
        # Клонировать репозиторий
        git_urls = list(repo_info.get('git_urls') or [])
        if not git_urls:
            fallback_git_url = str(repo_info.get('git_url') or '').strip()
            if fallback_git_url:
                git_urls = [fallback_git_url]
        repo_path = _clone_first_available_wiki_repo(git_urls, temp_dir)
        
        # Найти все markdown файлы и загрузить их
        for root, dirs, files in os.walk(repo_path):
            # Пропустить .git директорию
            if '.git' in dirs:
                dirs.remove('.git')
            
            for file in files:
                if not file.endswith('.md'):
                    continue
                
                full_path = os.path.join(root, file)
                # Относительный путь от корня репозитория
                rel_path = _normalize_wiki_rel_path(os.path.relpath(full_path, repo_path))
                
                # Восстановить URL страницы вики
                wiki_page_url = _restore_wiki_url_from_path(rel_path, wiki_root)
                logger.debug(f"[wiki-git] Файл: {rel_path} -> URL: {wiki_page_url}")
                
                # Загрузить содержимое файла
                try:
                    chunks = document_loader_manager.load_document(full_path, "md", options=loader_options)
                except Exception as e:
                    logger.warning(f"[wiki-git] Ошибка загрузки файла {rel_path}: {e}")
                    continue
                
                # Фильтруем пустые чанки
                chunks = [chunk for chunk in chunks if chunk.get('content', '').strip() and len(chunk.get('content', '').strip()) > 10]
                
                file_chunks = 0
                # Добавить чанки в базу знаний
                for chunk_no, chunk in enumerate(chunks, start=1):
                    metadata = _decorate_wiki_chunk_metadata(dict(chunk.get("metadata") or {}), rel_path)
                    metadata["wiki_root"] = wiki_root
                    metadata["original_url"] = wiki_page_url
                    metadata["wiki_page_url"] = wiki_page_url
                    chunk_title = _stable_chunk_title(chunk.get("title"), fallback=metadata.get("doc_title") or rel_path)
                    canonical_payload = rag_system._build_canonical_chunk_payload(
                        content=chunk.get("content", ""),
                        source_type="web",
                        source_path=wiki_page_url,
                        metadata=metadata,
                        chunk_no=chunk_no,
                        chunk_title=chunk_title,
                        chunk_columns={"parser_profile": "loader:web:wiki_git:v1"},
                    )
                    rag_system.add_chunk(
                        knowledge_base_id=knowledge_base_id,
                        content=chunk.get("content", ""),
                        source_type="web",
                        source_path=wiki_page_url,  # Используем восстановленный URL
                        metadata=canonical_payload["metadata"],
                        metadata_json=canonical_payload["metadata_json"],
                        chunk_columns=canonical_payload["chunk_columns"],
                        chunk_no=chunk_no,
                        chunk_title=chunk_title,
                    )
                    chunks_added += 1
                    file_chunks += 1
                
                files_processed += 1
                logger.info(f"[wiki-git] Обработан файл: {rel_path} -> {wiki_page_url} ({file_chunks} чанков)")
        
        logger.info(
            f"[wiki-git] Загрузка завершена: kb_id={knowledge_base_id}, "
            f"удалено={deleted_chunks}, файлов={files_processed}, фрагментов={chunks_added}"
        )
        
        return {
            "deleted_chunks": deleted_chunks,
            "files_processed": files_processed,
            "chunks_added": chunks_added,
            "wiki_root": wiki_root,
        }
    
    finally:
        # Удалить временную директорию
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"[wiki-git] Не удалось удалить временную директорию {temp_dir}: {e}")


def load_wiki_from_zip(
    zip_path: str,
    wiki_url: str,
    knowledge_base_id: int,
    loader_options: dict | None = None,
) -> Dict[str, any]:
    """
    Загрузить вики Gitee из ZIP архива и восстановить ссылки на оригинальные страницы.
    
    Args:
        zip_path: Путь к ZIP архиву с файлами вики
        wiki_url: URL вики (например, https://gitee.com/mazurdenis/open-harmony/wikis)
        knowledge_base_id: ID базы знаний
        session: SQLAlchemy сессия для записи в журнал загрузок (опционально)
    
    Returns:
        Словарь со статистикой загрузки и списком обработанных файлов
    """
    wiki_root = _normalize_base_url(wiki_url)
    
    # Удалить старые фрагменты этой вики
    logger.info(f"[wiki-zip] Удаление старых фрагментов вики: {wiki_root}")
    deleted_chunks = rag_system.delete_chunks_by_source_prefix(
        knowledge_base_id=knowledge_base_id,
        source_type="web",
        source_prefix=wiki_root,
    )
    
    chunks_added = 0
    files_processed = 0
    files_skipped = 0
    files_with_errors = 0
    processed_files = []  # Список обработанных файлов с их URL и статистикой
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Получить список всех файлов в архиве
            file_list = [name for name in zf.namelist() if not name.endswith('/')]
            logger.info(f"[wiki-zip] Найдено {len(file_list)} файлов в архиве")
            
            for file_name in file_list:
                # Пропустить служебные файлы
                if '.keep' in file_name.lower() or file_name.endswith('.keep'):
                    files_skipped += 1
                    continue
                
                # Обрабатываем только markdown файлы
                if not file_name.endswith('.md'):
                    files_skipped += 1
                    continue
                
                # Извлечь файл во временный файл
                try:
                    with zf.open(file_name) as src, tempfile.NamedTemporaryFile(delete=False, suffix='.md') as dst:
                        data = src.read()
                        dst.write(data)
                        temp_file_path = dst.name
                except Exception as e:
                    logger.warning(f"[wiki-zip] Ошибка извлечения файла {file_name}: {e}")
                    continue
                
                try:
                    # Восстановить URL страницы вики из пути файла
                    normalized_file_name = _normalize_wiki_rel_path(file_name)
                    wiki_page_url = _restore_wiki_url_from_path(normalized_file_name, wiki_root)
                    
                    # Загрузить содержимое файла
                    chunks = document_loader_manager.load_document(temp_file_path, "md", options=loader_options)
                    
                    # Фильтруем пустые чанки
                    chunks = [chunk for chunk in chunks if chunk.get('content', '').strip() and len(chunk.get('content', '').strip()) > 10]
                    
                    file_chunks = 0
                    # Добавить чанки в базу знаний
                    for chunk_no, chunk in enumerate(chunks, start=1):
                        metadata = _decorate_wiki_chunk_metadata(dict(chunk.get("metadata") or {}), normalized_file_name)
                        metadata["wiki_root"] = wiki_root
                        metadata["original_url"] = wiki_page_url
                        metadata["wiki_page_url"] = wiki_page_url
                        chunk_title = _stable_chunk_title(chunk.get("title"), fallback=metadata.get("doc_title") or normalized_file_name)
                        canonical_payload = rag_system._build_canonical_chunk_payload(
                            content=chunk.get("content", ""),
                            source_type="web",
                            source_path=wiki_page_url,
                            metadata=metadata,
                            chunk_no=chunk_no,
                            chunk_title=chunk_title,
                            chunk_columns={"parser_profile": "loader:web:wiki_zip:v1"},
                        )
                        rag_system.add_chunk(
                            knowledge_base_id=knowledge_base_id,
                            content=chunk.get("content", ""),
                            source_type="web",
                            source_path=wiki_page_url,  # Используем восстановленный URL
                            metadata=canonical_payload["metadata"],
                            metadata_json=canonical_payload["metadata_json"],
                            chunk_columns=canonical_payload["chunk_columns"],
                            chunk_no=chunk_no,
                            chunk_title=chunk_title,
                        )
                        chunks_added += 1
                        file_chunks += 1
                    
                    files_processed += 1
                    processed_files.append({
                        'file_name': normalized_file_name,
                        'wiki_url': wiki_page_url,
                        'chunks': file_chunks
                    })
                    logger.info(f"[wiki-zip] Обработан файл: {normalized_file_name} -> {wiki_page_url} ({file_chunks} чанков)")
                except Exception as e:
                    files_with_errors += 1
                    logger.warning(f"[wiki-zip] Ошибка обработки файла {normalized_file_name}: {e}", exc_info=True)
                finally:
                    # Удалить временный файл
                    try:
                        os.unlink(temp_file_path)
                    except Exception:
                        pass
        
        logger.info(
            f"[wiki-zip] Загрузка завершена: kb_id={knowledge_base_id}, "
            f"удалено={deleted_chunks}, файлов обработано={files_processed}, "
            f"пропущено={files_skipped}, ошибок={files_with_errors}, фрагментов={chunks_added}"
        )
        
        return {
            "deleted_chunks": deleted_chunks,
            "files_processed": files_processed,
            "chunks_added": chunks_added,
            "wiki_root": wiki_root,
            "processed_files": processed_files,  # Список обработанных файлов
        }
    
    except Exception as e:
        logger.error(f"[wiki-zip] Ошибка при обработке ZIP архива: {e}")
        raise


async def load_wiki_from_git_async(
    wiki_url: str,
    knowledge_base_id: int,
    loader_options: dict | None = None,
) -> Dict[str, int]:
    """
    Асинхронная обёртка для использования из Telegram-хэндлеров.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        load_wiki_from_git,
        wiki_url,
        knowledge_base_id,
        loader_options,
    )


async def load_wiki_from_zip_async(
    zip_path: str,
    wiki_url: str,
    knowledge_base_id: int,
    loader_options: dict | None = None,
) -> Dict[str, int]:
    """
    Асинхронная обёртка для использования из Telegram-хэндлеров.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        load_wiki_from_zip,
        zip_path,
        wiki_url,
        knowledge_base_id,
        loader_options,
    )
