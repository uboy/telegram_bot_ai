"""
Qdrant REST adapter for RAG dense retrieval.

Uses plain HTTP requests to avoid hard dependency on qdrant-client.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
import logging
import requests


logger = logging.getLogger(__name__)


@dataclass
class QdrantSearchResult:
    point_id: int
    score: float
    payload: Dict[str, Any]


class QdrantBackend:
    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        collection: str = "rag_chunks_v3",
        timeout_sec: float = 10.0,
    ) -> None:
        self.url = (url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.collection = (collection or "rag_chunks_v3").strip()
        self.timeout_sec = max(1.0, float(timeout_sec))
        self._ensured_vector_size: Optional[int] = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Qdrant backend is disabled (empty URL)")
        full_url = f"{self.url}{path}"
        resp = requests.request(
            method=method.upper(),
            url=full_url,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout_sec,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Qdrant {method} {path} failed: HTTP {resp.status_code} {resp.text[:300]}")
        if not resp.content:
            return {}
        data = resp.json()
        if isinstance(data, dict):
            return data
        return {"result": data}

    def ensure_collection(self, vector_size: int) -> None:
        if not self.enabled:
            return
        if vector_size <= 0:
            raise ValueError("vector_size must be > 0")
        if self._ensured_vector_size == int(vector_size):
            return
        path = f"/collections/{self.collection}"
        try:
            info = self._request("GET", path)
            result = info.get("result") or {}
            vectors = result.get("config", {}).get("params", {}).get("vectors") or {}
            size = vectors.get("size") if isinstance(vectors, dict) else None
            if size and int(size) != int(vector_size):
                logger.warning(
                    "Qdrant collection %s vector size mismatch: existing=%s requested=%s",
                    self.collection,
                    size,
                    vector_size,
                )
            else:
                self._ensured_vector_size = int(vector_size)
            return
        except Exception:
            pass

        payload = {
            "vectors": {
                "size": int(vector_size),
                "distance": "Cosine",
            },
            "optimizers_config": {"default_segment_number": 2},
        }
        self._request("PUT", path, payload)
        self._ensured_vector_size = int(vector_size)
        logger.info("Qdrant collection created: %s (size=%s)", self.collection, vector_size)

    def upsert_points(self, points: Iterable[Dict[str, Any]], wait: bool = True) -> int:
        items = list(points)
        if not items:
            return 0
        path = f"/collections/{self.collection}/points"
        if wait:
            path += "?wait=true"
        payload = {"points": items}
        self._request("PUT", path, payload)
        return len(items)

    def search(
        self,
        *,
        vector: List[float],
        limit: int,
        kb_id: int,
        source_types: Optional[List[str]] = None,
        path_prefixes: Optional[List[str]] = None,
    ) -> List[QdrantSearchResult]:
        must = [{"key": "kb_id", "match": {"value": int(kb_id)}}]
        if source_types:
            should = [{"key": "source_type", "match": {"value": s}} for s in source_types if s]
            if should:
                must.append({"should": should})
        payload = {
            "vector": vector,
            "limit": max(1, int(limit)),
            "with_payload": True,
            "with_vector": False,
            "filter": {"must": must},
        }
        data = self._request("POST", f"/collections/{self.collection}/points/search", payload)
        out: List[QdrantSearchResult] = []
        for item in data.get("result") or []:
            point_id = item.get("id")
            payload_obj = item.get("payload") or {}
            if point_id is None:
                continue
            try:
                point_id_int = int(point_id)
            except Exception:
                continue
            out.append(
                QdrantSearchResult(
                    point_id=point_id_int,
                    score=float(item.get("score") or 0.0),
                    payload=payload_obj if isinstance(payload_obj, dict) else {},
                )
            )
        if path_prefixes:
            prefixes = [p.lower() for p in path_prefixes if p]
            if prefixes:
                out = [
                    row
                    for row in out
                    if any(str(row.payload.get("source_path") or "").lower().startswith(pref) for pref in prefixes)
                ]
        return out

    def delete_by_filter(self, *, kb_id: int, source_type: str | None = None, source_path: str | None = None) -> None:
        must = [{"key": "kb_id", "match": {"value": int(kb_id)}}]
        if source_type:
            must.append({"key": "source_type", "match": {"value": source_type}})
        if source_path:
            must.append({"key": "source_path", "match": {"value": source_path}})
        payload = {"filter": {"must": must}}
        self._request("POST", f"/collections/{self.collection}/points/delete?wait=true", payload)

    def delete_kb(self, kb_id: int) -> None:
        self.delete_by_filter(kb_id=kb_id)
