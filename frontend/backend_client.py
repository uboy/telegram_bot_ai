"""
Клиент для обращения Telegram-бота к backend-сервису (FastAPI).

На первом этапе используется минимальный набор методов:
 - RAG-поиск по базе знаний
 - Получение списка баз знаний и источников

Позже сюда будет добавлена работа с пользователями и загрузкой документов.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from shared.logging_config import logger


class BackendClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 20.0) -> None:
        # По умолчанию используем адрес сервиса из docker-compose
        self.base_url = base_url or os.getenv("BACKEND_BASE_URL", "http://backend:8000")
        # Общий префикс API
        self.api_prefix = os.getenv("BACKEND_API_PREFIX", "/api/v1")
        self.timeout = timeout
        # Таймаут для RAG-запросов (может быть дольше из-за эмбеддингов/LLM)
        self.rag_timeout = float(os.getenv("BACKEND_RAG_TIMEOUT", "60.0"))
        # Таймаут для длительных операций (ingestion) - по умолчанию 5 минут
        self.ingestion_timeout = float(os.getenv("BACKEND_INGESTION_TIMEOUT", "300.0"))
        # API-ключ для авторизации запросов к backend (опционально)
        self.api_key = os.getenv("BACKEND_API_KEY", "")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.api_prefix}{path}"

    # === Аутентификация / пользователи ===

    def auth_telegram(self, telegram_id: str, username: str | None, full_name: str | None) -> Dict[str, Any]:
        """
        Создать/обновить пользователя по данным из Telegram на backend и вернуть его профиль.
        """
        url = self._url("/auth/telegram")
        payload: Dict[str, Any] = {
            "telegram_id": telegram_id,
            "username": username,
            "full_name": full_name,
        }
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.post(url, params=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при auth_telegram в backend: %s", e, exc_info=True)
            return {}

    def rag_query(
        self,
        query: str,
        knowledge_base_id: Optional[int] = None,
        top_k: int = 10,
        source_types: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
        path_prefixes: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Синхронный вызов RAG-поиска.
        Возвращает dict со структурой, совместимой с backend_service.schemas.rag.RAGAnswer.
        """
        payload: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "knowledge_base_id": knowledge_base_id,
        }
        if source_types:
            payload["source_types"] = source_types
        if languages:
            payload["languages"] = languages
        if path_prefixes:
            payload["path_prefixes"] = path_prefixes
        if date_from:
            payload["date_from"] = date_from
        if date_to:
            payload["date_to"] = date_to
        url = self._url("/rag/query")
        logger.debug("Backend RAG query: url=%s, payload=%r", url, payload)
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.rag_timeout, headers=headers) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                logger.debug("Backend RAG response: %r", data)
                return data
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при обращении к backend RAG: %s", e, exc_info=True)
            # Fallback: пустой ответ, чтобы бот мог корректно отреагировать
            return {"answer": "", "sources": []}

    def rag_summary(
        self,
        query: str,
        knowledge_base_id: Optional[int] = None,
        mode: str = "summary",
        top_k: int = 8,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "query": query,
            "knowledge_base_id": knowledge_base_id,
            "mode": mode,
            "top_k": top_k,
        }
        if date_from:
            payload["date_from"] = date_from
        if date_to:
            payload["date_to"] = date_to
        url = self._url("/rag/summary")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.rag_timeout, headers=headers) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при обращении к backend RAG summary: %s", e, exc_info=True)
            return {"answer": "", "sources": []}

    # === Ingestion ===

    def ingest_web_page(
        self,
        kb_id: int,
        url: str,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Загрузить одну веб-страницу в базу знаний через backend."""
        payload: Dict[str, Any] = {
            "knowledge_base_id": kb_id,
            "url": url,
            "telegram_id": telegram_id,
            "username": username,
        }
        url_api = self._url("/ingestion/web")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            # Используем увеличенный таймаут для ingestion операций
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при загрузке веб-страницы через backend: %s", e, exc_info=True)
            return {}

    # === Базы знаний / RAG ===

    def create_knowledge_base(self, name: str, description: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Создать новую базу знаний через backend."""
        url = self._url("/knowledge-bases/")
        payload: Dict[str, Any] = {"name": name, "description": description}
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при создании базы знаний через backend: %s", e, exc_info=True)
            return None

    # === Wiki ingestion ===

    def ingest_wiki_crawl(
        self,
        kb_id: int,
        url: str,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Рекурсивно обойти wiki-раздел и загрузить его через backend."""
        params: Dict[str, Any] = {
            "knowledge_base_id": kb_id,
            "url": url,
            "telegram_id": telegram_id,
            "username": username,
        }
        url_api = self._url("/ingestion/wiki-crawl")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            # Используем увеличенный таймаут для ingestion операций
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при wiki-crawl через backend: %s", e, exc_info=True)
            return {}

    def ingest_wiki_git(
        self,
        kb_id: int,
        url: str,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Загрузить вики из Git-репозитория через backend."""
        params: Dict[str, Any] = {
            "knowledge_base_id": kb_id,
            "url": url,
            "telegram_id": telegram_id,
            "username": username,
        }
        url_api = self._url("/ingestion/wiki-git")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            # Используем увеличенный таймаут для ingestion операций
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при wiki-git через backend: %s", e, exc_info=True)
            return {}

    def ingest_wiki_zip(
        self,
        kb_id: int,
        url: str,
        zip_bytes: bytes,
        filename: str,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Загрузить вики из ZIP архива через backend."""
        url_api = self._url("/ingestion/wiki-zip")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        files = {
            "file": (filename, zip_bytes, "application/zip"),
        }
        data: Dict[str, Any] = {
            "knowledge_base_id": str(kb_id),
            "url": url,
            "telegram_id": telegram_id or "",
            "username": username or "",
        }
        try:
            # Используем увеличенный таймаут для ingestion операций
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, data=data, files=files)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при wiki-zip через backend: %s", e, exc_info=True)
            return {}

    def ingest_document(
        self,
        kb_id: int,
        file_name: str,
        file_bytes: bytes,
        file_type: Optional[str] = None,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Загрузить документ или архив в базу знаний через backend."""
        url_api = self._url("/ingestion/document")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        files = {
            "file": (file_name, file_bytes, "application/octet-stream"),
        }
        data: Dict[str, Any] = {
            "knowledge_base_id": str(kb_id),
            "file_name": file_name,
        }
        if file_type is not None:
            data["file_type"] = file_type
        if telegram_id is not None:
            data["telegram_id"] = telegram_id
        if username is not None:
            data["username"] = username
        try:
            # Используем увеличенный таймаут для ingestion операций (загрузка документов может занимать много времени)
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, data=data, files=files)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при document ingestion через backend: %s", e, exc_info=True)
            return {}

    def ingest_image(
        self,
        kb_id: int,
        file_id: str,
        image_bytes: bytes,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Обработать изображение и добавить его в базу знаний через backend."""
        url_api = self._url("/ingestion/image")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        files = {
            "file": (f"{file_id}.jpg", image_bytes, "image/jpeg"),
        }
        data: Dict[str, Any] = {
            "knowledge_base_id": str(kb_id),
            "file_id": file_id,
        }
        if telegram_id is not None:
            data["telegram_id"] = telegram_id
        if username is not None:
            data["username"] = username
        if model is not None:
            data["model"] = model
        try:
            # Используем увеличенный таймаут для ingestion операций (обработка изображений может занимать время)
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, data=data, files=files)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при image ingestion через backend: %s", e, exc_info=True)
            return {}

    def get_job_status(self, job_id: int) -> Dict[str, Any]:
        url = self._url(f"/jobs/{job_id}")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при получении статуса job %s: %s", job_id, e, exc_info=True)
            return {}

    # === ASR (Voice to Text) ===

    def asr_transcribe(
        self,
        file_name: str,
        file_bytes: bytes,
        telegram_id: str,
        message_id: str,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        url_api = self._url("/asr/transcribe")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        files = {
            "file": (file_name, file_bytes, "audio/ogg"),
        }
        data: Dict[str, Any] = {
            "telegram_id": telegram_id,
            "message_id": message_id,
        }
        if language:
            data["language"] = language
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.post(url_api, data=data, files=files)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при ASR transcribe через backend: %s", e, exc_info=True)
            return {}

    def asr_job_status(self, job_id: str) -> Dict[str, Any]:
        url = self._url(f"/asr/jobs/{job_id}")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при получении статуса ASR job %s: %s", job_id, e, exc_info=True)
            return {}

    def get_asr_settings(self) -> Dict[str, Any]:
        url = self._url("/asr/settings")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при получении ASR настроек через backend: %s", e, exc_info=True)
            return {}

    def update_asr_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self._url("/asr/settings")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.put(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при обновлении ASR настроек через backend: %s", e, exc_info=True)
            return {}

    def ingest_codebase_path(
        self,
        kb_id: int,
        path: str,
        repo_label: Optional[str] = None,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Индексировать локальную кодовую базу через backend."""
        url_api = self._url("/ingestion/code-path")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        payload: Dict[str, Any] = {
            "knowledge_base_id": kb_id,
            "path": path,
            "repo_label": repo_label,
            "telegram_id": telegram_id,
            "username": username,
        }
        try:
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при индексировании кода (path) через backend: %s", e, exc_info=True)
            return {}

    def ingest_codebase_git(
        self,
        kb_id: int,
        git_url: str,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Индексировать кодовую базу из git через backend."""
        url_api = self._url("/ingestion/code-git")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        payload: Dict[str, Any] = {
            "knowledge_base_id": kb_id,
            "git_url": git_url,
            "telegram_id": telegram_id,
            "username": username,
        }
        try:
            with httpx.Client(timeout=self.ingestion_timeout, headers=headers) as client:
                resp = client.post(url_api, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при индексировании кода (git) через backend: %s", e, exc_info=True)
            return {}

    # === RAG utility ===

    def rag_reload_models(self) -> Dict[str, Any]:
        """Перезагрузить модели RAG через backend."""
        url_api = self._url("/rag/reload-models")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.post(url_api)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при перезагрузке моделей RAG через backend: %s", e, exc_info=True)
            return {}

    def list_knowledge_bases(self) -> List[Dict[str, Any]]:
        """Получить список баз знаний из backend."""
        url = self._url("/knowledge-bases/")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при обращении к backend (knowledge-bases): %s", e, exc_info=True)
            return []

    def list_knowledge_sources(self, kb_id: int) -> List[Dict[str, Any]]:
        """Получить список источников для указанной базы знаний."""
        url = self._url(f"/knowledge-bases/{kb_id}/sources")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Ошибка при обращении к backend (knowledge-bases/%s/sources): %s",
                kb_id,
                e,
                exc_info=True,
            )
            return []

    def get_kb_settings(self, kb_id: int) -> Dict[str, Any]:
        """Получить настройки базы знаний."""
        url = self._url(f"/knowledge-bases/{kb_id}/settings")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Ошибка при обращении к backend (knowledge-bases/%s/settings): %s",
                kb_id,
                e,
                exc_info=True,
            )
            return {}

    def update_kb_settings(self, kb_id: int, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Обновить настройки базы знаний."""
        url = self._url(f"/knowledge-bases/{kb_id}/settings")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        payload = {"settings": settings}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.put(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Ошибка при обновлении настроек KB %s через backend: %s",
                kb_id,
                e,
                exc_info=True,
            )
            return {}

    def get_import_log(self, kb_id: int) -> List[Dict[str, Any]]:
        """Получить журнал загрузок для указанной базы знаний."""
        url = self._url(f"/knowledge-bases/{kb_id}/import-log")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Ошибка при обращении к backend (knowledge-bases/%s/import-log): %s",
                kb_id,
                e,
                exc_info=True,
            )
            return []

    # === Пользователи ===

    def list_users(self) -> List[Dict[str, Any]]:
        """Получить список пользователей из backend."""
        url = self._url("/users/")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при обращении к backend (users): %s", e, exc_info=True)
            return []

    def toggle_user_role(self, user_id: int) -> bool:
        """Одобрить/сменить роль пользователя через backend."""
        url = self._url(f"/users/{user_id}/toggle-role")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.post(url)
                resp.raise_for_status()
                return True
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при изменении роли пользователя %s через backend: %s", user_id, e, exc_info=True)
            return False

    def delete_user(self, user_id: int) -> bool:
        """Удалить пользователя через backend."""
        url = self._url(f"/users/{user_id}")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.delete(url)
                resp.raise_for_status()
                return True
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при удалении пользователя %s через backend: %s", user_id, e, exc_info=True)
            return False

    def clear_knowledge_base(self, kb_id: int) -> bool:
        """Очистить базу знаний через backend."""
        url = self._url(f"/knowledge-bases/{kb_id}/clear")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.post(url)
                resp.raise_for_status()
                return True
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при очистке базы знаний %s через backend: %s", kb_id, e, exc_info=True)
            return False

    def delete_knowledge_base(self, kb_id: int) -> bool:
        """Удалить базу знаний через backend."""
        url = self._url(f"/knowledge-bases/{kb_id}")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                resp = client.delete(url)
                resp.raise_for_status()
                return True
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при удалении базы знаний %s через backend: %s", kb_id, e, exc_info=True)
            return False


# Глобальный экземпляр клиента для использования в боте
backend_client = BackendClient()


