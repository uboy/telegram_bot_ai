"""
Интеграция с n8n: HTTP-клиент для здоровья и отправки событий.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import requests

from shared.config import (
    N8N_BASE_URL,
    N8N_API_KEY,
    N8N_DEFAULT_WEBHOOK,
    N8N_TIMEOUT,
    N8N_ENABLED,
)

logger = logging.getLogger(__name__)


class N8NClient:
    """Простой клиент для взаимодействия с n8n."""

    def __init__(self) -> None:
        self.base_url = (N8N_BASE_URL or "").rstrip("/")
        self.api_key = N8N_API_KEY or ""
        self.default_webhook = (N8N_DEFAULT_WEBHOOK or "").strip("/")
        timeout = int(N8N_TIMEOUT or 5)
        self.timeout = max(timeout, 3)
        self.session = requests.Session()
        
        # Логирование статуса инициализации
        if not self.is_configured():
            logger.info("ℹ️ n8n отключен в конфигурации (N8N_BASE_URL не установлен)")
        else:
            logger.info(f"✅ n8n клиент инициализирован: {self.base_url}")

    def is_configured(self) -> bool:
        """Проверить, настроен ли n8n (включен в конфиге и URL указан)"""
        return N8N_ENABLED and bool(self.base_url)

    def has_webhook(self) -> bool:
        return bool(self.base_url and self.default_webhook)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return headers

    def health_check(self) -> Tuple[bool, str]:
        """Проверить доступность n8n через /healthz или /rest/health."""
        if not self.is_configured():
            return False, "N8N_BASE_URL не настроен"

        last_error = "n8n недоступен"
        for path in ("/healthz", "/rest/health"):
            url = f"{self.base_url}{path}"
            try:
                response = self.session.get(url, headers=self._headers(), timeout=self.timeout)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        status = data.get("status") or data.get("healthy") or "ok"
                        return True, f"Сервис доступен (status={status})"
                    except ValueError:
                        return True, "Сервис доступен"
                if response.status_code != 404:
                    return False, f"HTTP {response.status_code}: {response.text}"
            except Exception as exc:
                last_error = str(exc)
                logger.debug("Ошибка health-check n8n по %s: %s", url, exc)
        return False, last_error

    def send_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        webhook_path: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Отправить событие в n8n через webhook."""
        if not self.is_configured():
            return False, "N8N_BASE_URL не настроен"

        path = (webhook_path or self.default_webhook).strip("/")
        if not path:
            return False, "N8N_DEFAULT_WEBHOOK не настроен"

        url = f"{self.base_url}/webhook/{path}"
        body = {
            "event_type": event_type,
            "payload": payload,
        }

        try:
            response = self.session.post(url, json=body, headers=self._headers(), timeout=self.timeout)
            if response.status_code in (200, 201, 202, 204):
                return True, "ok"
            return False, f"HTTP {response.status_code}: {response.text}"
        except Exception as exc:
            logger.error("Ошибка отправки события в n8n: %s", exc)
            return False, str(exc)

    def trigger_workflow(self, workflow_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """Запустить workflow по ID через API (требует API-ключ)."""
        if not self.is_configured():
            return False, "N8N_BASE_URL не настроен"
        if not workflow_id:
            return False, "workflow_id не указан"

        url = f"{self.base_url}/api/v1/workflows/{workflow_id}/run"
        try:
            response = self.session.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
            if response.status_code in (200, 201, 202, 204):
                return True, "ok"
            return False, f"HTTP {response.status_code}: {response.text}"
        except Exception as exc:
            logger.error("Ошибка запуска workflow n8n: %s", exc)
            return False, str(exc)


n8n_client = N8NClient()

