from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from telegram.request import HTTPXRequest

_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def _normalize_env_value(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def get_proxy_env_settings() -> Dict[str, Optional[str]]:
    return {key: _normalize_env_value(os.getenv(key)) for key in _PROXY_ENV_KEYS}


def get_telegram_proxy_url() -> Optional[str]:
    settings = get_proxy_env_settings()
    return settings.get("HTTPS_PROXY") or settings.get("ALL_PROXY") or settings.get("HTTP_PROXY")


def redact_proxy_url(value: Optional[str]) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return "<configured>"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def build_httpx_client(*, timeout: Any, headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> httpx.Client:
    client_kwargs = dict(kwargs)
    client_kwargs.setdefault("timeout", timeout)
    client_kwargs.setdefault("headers", headers)
    client_kwargs["trust_env"] = True
    return httpx.Client(**client_kwargs)


def build_telegram_request(
    *,
    proxy_url: Optional[str] = None,
    connect_timeout: float = 10.0,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    pool_timeout: float = 5.0,
) -> HTTPXRequest:
    effective_proxy = _normalize_env_value(proxy_url) or get_telegram_proxy_url()
    return HTTPXRequest(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        write_timeout=write_timeout,
        pool_timeout=pool_timeout,
        proxy=effective_proxy,
        httpx_kwargs={"trust_env": True},
    )


def log_proxy_configuration(logger: logging.Logger, component: str) -> None:
    settings = get_proxy_env_settings()
    outbound_proxy = settings.get("HTTPS_PROXY") or settings.get("ALL_PROXY") or settings.get("HTTP_PROXY")
    logger.info(
        "%s proxy configuration: enabled=%s proxy=%s no_proxy=%s",
        component,
        bool(outbound_proxy),
        redact_proxy_url(outbound_proxy) if outbound_proxy else "unset",
        "set" if settings.get("NO_PROXY") else "unset",
    )
