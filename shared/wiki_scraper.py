"""
Рекурсивный скрепер wiki-сайтов с загрузкой в базу знаний RAG.

Особенности:
- Обходит страницы только в пределах заданного URL-вики (домен + path-префикс).
- Не уходит на другие сайты и другие разделы (например, вне /wikis).
- Перед пересборкой вики удаляет старые фрагменты по префиксу URL.
"""

import asyncio
from collections import deque
from typing import Dict, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from shared.rag_system import rag_system
from shared.document_loaders import document_loader_manager
from shared.logging_config import logger


def _normalize_base_url(base_url: str) -> str:
    """Нормализовать базовый URL вики и определить корень wiki-раздела.

    Для URL вида https://gitee.com/mazurdenis/open-harmony/wikis/Environment/...
    вернет https://gitee.com/mazurdenis/open-harmony/wikis
    """
    url = (base_url or "").strip()
    if not url:
        raise ValueError("URL вики не указан")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    path = parsed.path

    # Найти сегмент /wikis и обрезать все, что после него
    wiki_index = path.find("/wikis")
    if wiki_index != -1:
        wiki_path = path[: wiki_index + len("/wikis")]
    else:
        wiki_path = path

    normalized_path = wiki_path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"


def _same_wiki_scope(base_prefix: str, candidate_url: str) -> bool:
    """
    Проверить, относится ли ссылка к той же вики:
    - тот же домен и схема,
    - путь начинается с базового префикса (например, /mazurdenis/open-harmony/wikis).
    """
    if not candidate_url:
        return False

    # Отсечь якоря и пустые ссылки
    if candidate_url.startswith(("#", "mailto:", "javascript:")):
        return False

    # Нормализуем относительные ссылки позже, здесь ожидаем уже абсолютный URL
    return candidate_url.startswith(base_prefix)


def crawl_wiki_to_kb(
    base_url: str,
    knowledge_base_id: int,
    max_pages: int = 200,
    loader_options: dict | None = None,
) -> Dict[str, int]:
    """
    Рекурсивно обойти wiki-раздел сайта и загрузить страницы в базу знаний.

    - base_url: корневая страница вики (например, https://gitee.com/mazurdenis/open-harmony/wikis)
    - knowledge_base_id: ID базы знаний RAG
    - max_pages: ограничение на количество страниц (на всякий случай, от DoS)
    """
    wiki_root = _normalize_base_url(base_url)

    # Удалить старые фрагменты этой вики в выбранной БЗ
    logger.info("[wiki] старт сканирования: kb_id=%s, base_url=%s, wiki_root=%s", knowledge_base_id, base_url, wiki_root)

    deleted_chunks = rag_system.delete_chunks_by_source_prefix(
        knowledge_base_id=knowledge_base_id,
        source_type="web",
        source_prefix=wiki_root,
    )

    visited: Set[str] = set()
    queue: deque[str] = deque([wiki_root, base_url.strip()])

    pages_processed = 0
    chunks_added = 0

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    def fetch_with_retry(url: str, max_retries: int = 3, base_delay: float = 2.0):
        """Загрузить URL с повторными попытками при таймауте"""
        for attempt in range(max_retries):
            try:
                timeout = 15 + (attempt * 5)  # Увеличиваем таймаут с каждой попыткой
                resp = requests.get(url, timeout=timeout, headers=headers)
                resp.raise_for_status()
                return resp
            except requests.exceptions.Timeout as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Экспоненциальная задержка
                    logger.warning("[wiki] Таймаут запроса %s (попытка %d/%d), повтор через %.1f сек: %s", 
                                 url, attempt + 1, max_retries, delay, e)
                    import time
                    time.sleep(delay)
                else:
                    logger.error("[wiki] Таймаут запроса %s после %d попыток: %s", url, max_retries, e)
                    raise
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("[wiki] Ошибка запроса %s (попытка %d/%d), повтор через %.1f сек: %s", 
                                 url, attempt + 1, max_retries, delay, e)
                    import time
                    time.sleep(delay)
                else:
                    logger.error("[wiki] Ошибка запроса %s после %d попыток: %s", url, max_retries, e)
                    raise
        return None

    while queue and pages_processed < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        try:
            resp = fetch_with_retry(url)
            if resp is None:
                continue
        except Exception as e:
            logger.warning("[wiki] Не удалось загрузить %s после всех попыток: %s", url, e)
            continue

        # Загрузить содержимое страницы как web-документ через существующий загрузчик
        try:
            chunks = document_loader_manager.load_document(url, "web", options=loader_options)
        except Exception as e:
            logger.warning("[wiki] ошибка загрузки содержимого %s: %s", url, e)
            chunks = []

        for chunk in chunks:
            metadata = dict(chunk.get("metadata") or {})
            # Добавим метку корня вики, чтобы можно было дополнительно фильтровать при необходимости
            metadata.setdefault("wiki_root", wiki_root)
            # Сохраняем оригинальный URL страницы для нормализации при отображении
            metadata["original_url"] = url
            metadata["wiki_page_url"] = url  # Для совместимости
            rag_system.add_chunk(
                knowledge_base_id=knowledge_base_id,
                content=chunk.get("content", ""),
                source_type="web",
                source_path=url,
                metadata=metadata,
            )
            chunks_added += 1

        pages_processed += 1

        # Парсим ссылки для рекурсивного обхода
        try:
            soup = BeautifulSoup(resp.content, "html.parser")
        except Exception as e:
            logger.warning("[wiki] ошибка парсинга HTML %s: %s", url, e)
            continue

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue

            # Убрать якорь (#...) — он не меняет содержимое страницы
            if "#" in href:
                href = href.split("#", 1)[0].strip()
                if not href:
                    continue

            # Преобразовать в абсолютный URL
            absolute_url = urljoin(url, href)

            # Ограничить обход только текущим wiki-разделом
            if not _same_wiki_scope(wiki_root, absolute_url):
                continue

            if absolute_url not in visited:
                queue.append(absolute_url)

    logger.info(
        "[wiki] завершено: kb_id=%s, wiki_root=%s, удалено=%s, страниц=%s, фрагментов=%s",
        knowledge_base_id,
        wiki_root,
        deleted_chunks,
        pages_processed,
        chunks_added,
    )

    return {
        "deleted_chunks": deleted_chunks,
        "pages_processed": pages_processed,
        "chunks_added": chunks_added,
        "wiki_root": wiki_root,
    }


async def crawl_wiki_to_kb_async(
    base_url: str,
    knowledge_base_id: int,
    max_pages: int = 200,
    loader_options: dict | None = None,
) -> Dict[str, int]:
    """
    Асинхронная обёртка для использования из Telegram-хэндлеров.
    Запускает синхронный сканер в отдельном потоке.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        crawl_wiki_to_kb,
        base_url,
        knowledge_base_id,
        max_pages,
        loader_options,
    )



